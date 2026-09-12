//! Издержки и проскальзывание по Decision 15 (план, §5.3).
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
//! Вердикт «эдж после издержек положителен» больше не выносится здесь
//! (таск 17 снял `VerdictCell`/`G3Verdict`/`select_verdict_cell`/`decide_g3*`
//! — построены под отменённую ячеечную модель C1/C2 §5.2, `interfaces.md`
//! «Что построено под отменённый дизайн»; ни один вызывающий не читал их —
//! живой вердикт на очереди даёт `backtest::G4Verdict`/`decide_g4`).
//! Зелёный порог H6 (`GREEN_NET_BPS`, средний net ≥ 3 bps) остаётся здесь
//! просто числом: `shortlist` сравнивает с ним готовый `net_fill`.
//!
//! Модуль не хранит состояния и не выделяет память.

use crate::lob::cells::joint_product_interval;
use crate::lob::levels::LevelRecord;
use crate::lob::markout::{base_before, markout_bps, sample_asof, MidSample};
use crate::stats::{count_f64, count_f64_u64};
use std::collections::BTreeMap;

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

/// Наблюдение `net` для уровня на горизонте `horizon_ms`: markout от базы
/// Decision 11 (последний срез строго до смерти) до среза «как есть» на
/// `base + horizon` и спред того же среза выхода. Одна конструкция на всех
/// читателей (`lob pilot`, `lob dashboard`; до таска 33 каждый нёс свою
/// копию петли `base_before` → срез выхода → `Observation`). `None` — нет
/// базы, нет среза на горизонте или середина не положительна.
pub fn observation_at(
    level: &LevelRecord,
    mids: &[MidSample],
    horizon_ms: i64,
) -> Option<Observation> {
    let (base_ts, base2x) = base_before(mids, level.death_ms)?;
    let exit = sample_asof(mids, base_ts, horizon_ms)?;
    let m_bps = markout_bps(
        level.side,
        base2x,
        crate::lob::markout::mid_double_tick(exit.bid_tick, exit.ask_tick),
    )?;
    Some(Observation {
        m_bps,
        spread_ticks_exit: exit.ask_tick - exit.bid_tick,
        mid2x_base: base2x,
    })
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
    // Числитель — тики единиц–десятков × 10⁴ (~1e5–1e6), знаменатель —
    // удвоенная середина в e9 (~1e13–1e14): оба точны в f64 (< 2^53).
    #[allow(clippy::cast_precision_loss)]
    let (num_f, den_f) = (num as f64, mid2x_base as f64);
    Some(num_f / den_f)
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
    Some(sum / count_f64(observations.len()))
}

// ---------------------------------------------------------------------------
// `net_fill` (таск 03, истории 19–22, §6): «вот это и есть окупается».
// ---------------------------------------------------------------------------

/// Одно наблюдение для `net_fill`: `net` после издержек (см. `net_bps`),
/// признак исполнения входа за 2 с (R06) и сутки как целый кластер для
/// совместного бутстрапа — тот же смысл кластера, что у
/// `stats::wild_cluster_bootstrap_t` (сравнение и группировка по целому,
/// не по `f64`, A1).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FillObservation {
    /// Сутки UTC как целый кластер.
    pub day_cluster: i64,
    /// `net` этого наблюдения в bps (после издержек).
    pub net_bps: f64,
    /// Исполнился ли вход за 2 с (Decision, R06). `false` — не исполнился
    /// или позиция уже была открыта; какая из двух причин — не поле этой
    /// структуры (см. CONCERNS: колонки причины пропуска — гейт Е/задача 09).
    pub filled: bool,
}

/// Точечный `net_fill` в bps — по наблюдению, буквально `Σ netᵢ·fillᵢ / N`
/// (R07): НЕ произведение отдельно посчитанных средних `net` и доли `fill`
/// — `fill` коррелирует с исходом (бриф), и подмена суммы произведением
/// агрегатов дала бы другое число на тех же данных. `None` — срез пуст или
/// хотя бы один `net_bps` не конечен: молча пропускать наблюдение значило
/// бы пересчитывать выборку задним числом (то же правило, что в
/// `mean_net_bps`).
pub fn net_fill_bps(observations: &[FillObservation]) -> Option<f64> {
    if observations.is_empty() {
        return None;
    }
    let mut sum = 0.0;
    for o in observations {
        if !o.net_bps.is_finite() {
            return None;
        }
        let fill_indicator = if o.filled { 1.0 } else { 0.0 };
        sum += o.net_bps * fill_indicator;
    }
    Some(sum / count_f64(observations.len()))
}

/// Доля исполнений (`fill`), R06: печатается отдельной колонкой всегда, а
/// не только в вердикте (см. `format_fill_column`). `None` — срез пуст.
pub fn fill_rate(observations: &[FillObservation]) -> Option<f64> {
    if observations.is_empty() {
        return None;
    }
    let filled = observations.iter().filter(|o| o.filled).count();
    Some(count_f64(filled) / count_f64(observations.len()))
}

/// Готовая колонка `fill` для CSV/отчёта — R06 требует её печатать всегда,
/// а не только внутри вердикта. Проводка в писатель `shortlist`/`commands`
/// (task 06/10) — эта функция лишь форматирует уже посчитанное число,
/// колонки не изобретает.
pub fn format_fill_column(rate: Option<f64>) -> String {
    match rate {
        Some(v) => format!("{v:.4}"),
        None => "none".to_string(),
    }
}

/// Итог совместного интервала `net_fill` (R08/R50): точка (тождественно
/// `net_fill_bps` на тех же данных — алгебраическое тождество, не оценка:
/// `Σᵢ netᵢ·fillᵢ = Σ_{fillᵢ=1} netᵢ`, значит `mean(net|fill=1)·fill_rate
/// = (Σ_{fill=1}netᵢ/n_filled)·(n_filled/N) = Σ netᵢ·fillᵢ/N`) и нижняя
/// граница — то, что сравнивается с нулём («окупается», бриф).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NetFillInterval {
    /// Исполнившихся входов, суммарно по суткам.
    pub n_filled: u64,
    /// Всего входов (знаменатель `N`), суммарно по суткам.
    pub n_total: u64,
    /// Точка: `net_fill`, bps.
    pub point_bps: f64,
    /// Нижняя граница интервала, bps.
    pub lower_bps: f64,
    /// Число реплик.
    pub replications: u32,
    /// Seed реплик.
    pub seed: u64,
}

/// Нижняя граница интервала `net_fill` совместным ресэмплингом пары
/// `(net, fill)`: один вес Уэбба на обе серии в одной реплике, произведение
/// считается на реплику (R08/R50) — расширение `cells::disjoint_contrast`
/// (`cells::joint_product_interval`), а не второй бутстрап. Наблюдения
/// группируются по `day_cluster` в `BTreeMap`, как `stats::group_by_cluster`:
/// порядок обхода детерминирован и не зависит от порядка входного среза
/// (ARCHITECTURE.md A2). Разложение на два ряда — среднее `net` там, где
/// `fill = true` (числитель `Σnetᵢ`, знаменатель `n_filled`), и доля `fill`
/// (числитель `n_filled` тех же суток, знаменатель `n_total`) — таково, что
/// их произведение на наблюдённых данных равно точке `net_fill_bps` (см.
/// `NetFillInterval`), а на репликах — совместному ресэмплингу с общим
/// весом на сутки, как того требует критерий.
///
/// `alpha` — односторонний уровень нижнего перцентиля, обязательный
/// параметр без умолчания (§9 плана: изобретённое число запрещено; спека
/// не называет число для этого гейта, в отличие от `stats::GATE_ALPHA` —
/// той альфы гейта G2). `None` — срез пуст, хотя бы один `net_bps` не
/// конечен, или исполнений нет вовсе (числитель `mean(net|filled)` не из
/// чего считать).
#[allow(clippy::cast_precision_loss)]
pub fn net_fill_interval(
    observations: &[FillObservation],
    alpha: f64,
    replications: u32,
    seed: u64,
) -> Option<NetFillInterval> {
    if observations.is_empty() || observations.iter().any(|o| !o.net_bps.is_finite()) {
        return None;
    }
    let mut by_day: BTreeMap<i64, (f64, u64, u64)> = BTreeMap::new();
    for o in observations {
        let entry = by_day.entry(o.day_cluster).or_insert((0.0, 0, 0));
        entry.2 = entry.2.saturating_add(1);
        if o.filled {
            entry.0 += o.net_bps;
            entry.1 = entry.1.saturating_add(1);
        }
    }
    let day_sums: Vec<(f64, u64, f64, u64)> = by_day
        .values()
        .map(|&(sum_net_filled, n_filled, n_total)| {
            (sum_net_filled, n_filled, count_f64_u64(n_filled), n_total)
        })
        .collect();
    let jp = joint_product_interval(&day_sums, alpha, replications, seed)?;
    Some(NetFillInterval {
        n_filled: jp.n_a,
        n_total: jp.n_b,
        point_bps: jp.point,
        lower_bps: jp.lower,
        replications: jp.replications,
        seed: jp.seed,
    })
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

    /// H6 — число само по себе: 3 bps, и никакая функция этого модуля не
    /// принимает по нему решение (таск 17 снял вердиктную ячейку C1/C2 и
    /// гейт G3, который раньше стоял на этом месте — см. докстрока модуля).
    /// `net_bps` ниже зелёного порога остаётся валидным числом, не отказом:
    /// сравнение с порогом — дело вызывающего (`shortlist`).
    #[test]
    fn green_threshold_is_a_number_not_a_verdict_here() {
        assert!(close(GREEN_NET_BPS, 3.0));
        let probe = net_bps(10.0, 1, 20_000).expect("синтетика обязана посчитаться");
        assert!(
            probe.is_finite() && probe < GREEN_NET_BPS,
            "ниже зелёного, но конечно"
        );
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

    fn fobs(day: i64, net: f64, filled: bool) -> FillObservation {
        FillObservation {
            day_cluster: day,
            net_bps: net,
            filled,
        }
    }

    /// R07 буквально: `Σ netᵢ·fillᵢ / N`, не среднее только по исполнившимся
    /// и не произведение агрегатов. Четыре наблюдения, два исполнились —
    /// сумма `10 + (-4) = 6`, знаменатель `N = 4`, точка `1.5`, не `3.0`
    /// (среднее по исполнившимся) и не `mean(net)*fill_rate = 3.0*0.5=1.5`
    /// случайно совпавшее здесь — проверяется отдельно ниже, что формула не
    /// вырождается в это совпадение.
    #[test]
    fn net_fill_bps_is_sum_of_products_over_n_not_mean_of_filled() {
        let data = [
            fobs(0, 10.0, true),
            fobs(0, -4.0, true),
            fobs(1, 100.0, false),
            fobs(1, -100.0, false),
        ];
        let nf = net_fill_bps(&data).expect("непустой срез обязан посчитаться");
        assert!(close(nf, 1.5), "получено {nf}");
        let mean_only_filled = (10.0 + -4.0) / 2.0;
        assert!(
            !close(nf, mean_only_filled),
            "net_fill не обязан совпасть со средним только по исполнившимся"
        );
    }

    /// Отказы: пустой срез и хотя бы одно неконечное `net_bps` — `None`, не
    /// молчаливый пропуск (то же правило, что `mean_net_bps`).
    #[test]
    fn net_fill_bps_refuses_on_empty_or_nonfinite() {
        assert_eq!(net_fill_bps(&[]), None);
        assert_eq!(net_fill_bps(&[fobs(0, f64::NAN, true)]), None);
        assert_eq!(net_fill_bps(&[fobs(0, f64::INFINITY, false)]), None);
    }

    /// R06: `fill` — своя колонка, печатается всегда, не только в вердикте.
    #[test]
    fn fill_rate_is_its_own_column_always() {
        let data = [
            fobs(0, 1.0, true),
            fobs(0, 1.0, false),
            fobs(0, 1.0, true),
            fobs(0, 1.0, false),
        ];
        let rate = fill_rate(&data).expect("непустой срез обязан посчитаться");
        assert!(close(rate, 0.5));
        assert_eq!(format_fill_column(Some(rate)), "0.5000");
        assert_eq!(format_fill_column(None), "none");
        assert_eq!(fill_rate(&[]), None);
    }

    /// `NetFillInterval.point_bps` обязан совпасть с `net_fill_bps` на тех
    /// же данных (тождество разложения `mean(net|fill=1)·fill_rate`, см.
    /// докстринг `NetFillInterval`), а нижняя граница — быть конечной и не
    /// выше точки при явно положительном сигнале на многих сутках.
    #[test]
    fn net_fill_interval_point_matches_direct_formula() {
        let mut data = Vec::new();
        for day in 0..8i64 {
            data.push(fobs(day, 10.0, true));
            data.push(fobs(day, 8.0, true));
            data.push(fobs(day, -1.0, false));
        }
        let direct = net_fill_bps(&data).expect("обязано посчитаться");
        let interval = net_fill_interval(&data, 0.05, 999, 7).expect("обязано посчитаться");
        assert!(
            close(interval.point_bps, direct),
            "точка интервала {} обязана совпасть с прямой формулой {direct}",
            interval.point_bps
        );
        assert_eq!(interval.n_filled, 16);
        assert_eq!(interval.n_total, 24);
        assert!(interval.lower_bps.is_finite());
        assert!(interval.lower_bps <= interval.point_bps);
    }

    /// Отказы интервала: пустой срез, неконечный `net_bps`, и (отдельно)
    /// нет ни одного исполнения — числителю `mean(net|fill=1)` не из чего
    /// считаться, `joint_product_interval` обязан вернуть `None` по своему
    /// собственному правилу «ряд пуст».
    #[test]
    fn net_fill_interval_refuses_on_empty_nonfinite_or_no_fills() {
        assert_eq!(net_fill_interval(&[], 0.05, 999, 1), None);
        assert_eq!(
            net_fill_interval(&[fobs(0, f64::NAN, true)], 0.05, 999, 1),
            None
        );
        let no_fills = [fobs(0, 5.0, false), fobs(0, 6.0, false)];
        assert_eq!(net_fill_interval(&no_fills, 0.05, 999, 1), None);
    }

    /// Наивный процентильный нижний перцентиль ОДНОГО ряда при кластерном
    /// wild-бутстрапе — контрпример, от которого предостерегает критерий
    /// приёмки: если вызвать это отдельно для «net при исполнении» и
    /// отдельно для «доли исполнений» с независимыми seed, а потом
    /// перемножить нижние границы, кросс-суточная связь между рядами
    /// пропадает без следа (в отличие от `net_fill_interval`, где один и
    /// тот же вес Уэбба взвешивает оба ряда в одной реплике). Веса — тот же
    /// `stats::webb_weight` по тому же индексу `next_u64() % 6`, каким
    /// пользуется и `cells::next_webb`: тест не заводит второй генератор,
    /// а собирает его из уже открытых внутри крейта частей `stats` — так
    /// же, как это делает сам `cells.rs` (см. докстринг `next_webb`).
    #[allow(clippy::cast_possible_truncation)]
    fn naive_series_lower(
        day_sums: &[(f64, u64)],
        denom: u64,
        alpha: f64,
        replications: u32,
        seed: u64,
    ) -> f64 {
        let mut rng = crate::stats::SplitMix64::new(seed);
        let mut reps: Vec<f64> = Vec::with_capacity(replications as usize);
        for _ in 0..replications {
            let mut boot = 0.0;
            for &(num, _n) in day_sums {
                let idx = (rng.next_u64() % 6) as u32;
                boot += crate::stats::webb_weight(idx) * num;
            }
            reps.push(boot / count_f64_u64(denom));
        }
        reps.sort_by(|a, b| a.total_cmp(b));
        let n = reps.len();
        let pos = alpha * (n - 1) as f64;
        let lo = pos.floor() as usize;
        let hi = pos.ceil() as usize;
        if lo == hi {
            reps[lo]
        } else {
            reps[lo] + (reps[hi] - reps[lo]) * (pos - lo as f64)
        }
    }

    /// Наблюдения для `DAYS` суток, где сутки чередуют два известных режима:
    /// «сильный» (высокий `net`, высокая доля исполнений) и «слабый»
    /// (отрицательный `net`, низкая доля исполнений) — известная по
    /// построению положительная ковариация между `net` и `fill` день ко дню
    /// (бриф: «`fill` коррелирует с исходом»). `regime_plus(day)` решает,
    /// какой режим у суток `day`.
    fn regime_observations(
        days: i64,
        n_total: u64,
        net_plus: f64,
        n_filled_plus: u64,
        net_minus: f64,
        n_filled_minus: u64,
        regime_plus: impl Fn(i64) -> bool,
    ) -> Vec<FillObservation> {
        let mut out = Vec::with_capacity(days as usize * n_total as usize);
        for day in 0..days {
            let (net_d, n_filled_d) = if regime_plus(day) {
                (net_plus, n_filled_plus)
            } else {
                (net_minus, n_filled_minus)
            };
            for i in 0..n_total {
                out.push(fobs(day, net_d, i < n_filled_d));
            }
        }
        out
    }

    /// То же самое разложением на два ряда по суткам — то, что кормит
    /// «наивный» контрпример напрямую (числитель/знаменатель, без прохода
    /// по `n_total` наблюдениям на сутки).
    fn regime_day_sums(
        days: i64,
        n_total: u64,
        net_plus: f64,
        n_filled_plus: u64,
        net_minus: f64,
        n_filled_minus: u64,
        regime_plus: impl Fn(i64) -> bool,
    ) -> Vec<(f64, u64, f64, u64)> {
        (0..days)
            .map(|day| {
                let (net_d, n_filled_d) = if regime_plus(day) {
                    (net_plus, n_filled_plus)
                } else {
                    (net_minus, n_filled_minus)
                };
                (
                    net_d * count_f64_u64(n_filled_d),
                    n_filled_d,
                    count_f64_u64(n_filled_d),
                    n_total,
                )
            })
            .collect()
    }

    /// R08/R50, критерий 1: синтетика с известной ковариацией между `net`
    /// (при исполнении) и долей исполнений — сутки чередуют «сильный» и
    /// «слабый» режим поровну, так что истинный `net_fill` в популяции
    /// известен по замкнутой форме: `τ = 0.5·(netₚ·nₚ + netₘ·nₘ)/n_total`
    /// (доказательство в докстринге функции: при равновероятном режиме
    /// `E[Σ netᵢfillᵢ]/N` телескопируется ровно в эту сумму). Через много
    /// независимых выборок (разные сутки-режимы по монете на каждый прогон)
    /// наивное произведение отдельно пробутстрапленных границ и совместный
    /// интервал обязаны разойтись, а совместная нижняя граница — накрывать
    /// `τ` не реже заявленной частоты `1 − alpha` (валидная односторонняя
    /// граница может быть консервативнее, но не чаще недокрывать).
    #[test]
    fn joint_interval_diverges_from_naive_product_and_covers_truth_under_known_covariance() {
        const N_TOTAL: u64 = 100;
        const N_FILLED_PLUS: u64 = 80;
        const N_FILLED_MINUS: u64 = 20;
        const NET_PLUS: f64 = 8.0;
        const NET_MINUS: f64 = -6.0;
        const DAYS: i64 = 10;
        const ALPHA: f64 = 0.05;
        const REPLICATIONS: u32 = 300;
        const OUTER: u32 = 300;

        let true_net_fill = 0.5
            * (NET_PLUS * count_f64_u64(N_FILLED_PLUS) + NET_MINUS * count_f64_u64(N_FILLED_MINUS))
            / count_f64_u64(N_TOTAL);
        assert!(
            close(true_net_fill, 2.6),
            "замкнутая форма — свидетельство теста"
        );

        let mut joint_covers = 0u32;
        let mut sum_abs_gap = 0.0;
        let mut reversed_verdict = 0u32; // naive says "окупается", joint — нет.

        for trial in 0..OUTER {
            let mut coin = crate::stats::SplitMix64::new(9_000_000 + u64::from(trial));
            // Однократный бросок монеты на сутки, тот же порядок для
            // наблюдений и для их суточного разложения.
            let flips: Vec<bool> = (0..DAYS)
                .map(|_| coin.next_u64().is_multiple_of(2))
                .collect();
            let regime = |day: i64| flips[day as usize];

            let observations = regime_observations(
                DAYS,
                N_TOTAL,
                NET_PLUS,
                N_FILLED_PLUS,
                NET_MINUS,
                N_FILLED_MINUS,
                regime,
            );
            let interval = net_fill_interval(
                &observations,
                ALPHA,
                REPLICATIONS,
                1_000_000 + u64::from(trial),
            )
            .expect("оба режима дают исполнения");

            let day_sums = regime_day_sums(
                DAYS,
                N_TOTAL,
                NET_PLUS,
                N_FILLED_PLUS,
                NET_MINUS,
                N_FILLED_MINUS,
                regime,
            );
            let total_n_filled: u64 = day_sums.iter().map(|d| d.1).sum();
            let a_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.0, d.1)).collect();
            let b_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.2, d.3)).collect();
            let naive_a = naive_series_lower(
                &a_series,
                total_n_filled,
                ALPHA,
                REPLICATIONS,
                2_000_000 + u64::from(trial),
            );
            let naive_b = naive_series_lower(
                &b_series,
                u64::try_from(DAYS).unwrap_or(0) * N_TOTAL,
                ALPHA,
                REPLICATIONS,
                3_000_000 + u64::from(trial),
            );
            let naive_lower = naive_a * naive_b;

            sum_abs_gap += (interval.lower_bps - naive_lower).abs();
            if interval.lower_bps <= true_net_fill {
                joint_covers += 1;
            }
            if naive_lower > 0.0 && interval.lower_bps <= 0.0 {
                reversed_verdict += 1;
            }
        }

        let mean_abs_gap = sum_abs_gap / f64::from(OUTER);
        assert!(
            mean_abs_gap > 1.0,
            "наивное произведение и совместный интервал обязаны расходиться заметно: {mean_abs_gap}"
        );

        let joint_coverage = f64::from(joint_covers) / f64::from(OUTER);
        assert!(
            joint_coverage >= 1.0 - ALPHA - 0.05,
            "совместный интервал обязан накрывать истину не реже заявленной частоты {}: получено {joint_coverage}",
            1.0 - ALPHA
        );

        let reversal_rate = f64::from(reversed_verdict) / f64::from(OUTER);
        assert!(
            reversal_rate >= 0.8,
            "в большинстве прогонов наивный метод обязан ошибочно объявлять «окупается», \
             пока совместный корректно воздерживается: доля {reversal_rate}"
        );
    }

    /// R08/R50, критерий 2: при отрицательной нижней границе `net`
    /// (составляющей `net_fill`) наивное произведение отдельно
    /// пробутстрапленных границ даёт бессмыслицу — умножение двух
    /// «не уверены, что положительно» границ (`net`-компонента и доля
    /// исполнений обе дают отрицательную наивную нижнюю границу) молча
    /// переворачивает знак и выдаёт уверенно положительное число, — тогда
    /// как совместный интервал остаётся отрицательным, то есть корректно
    /// отказывается признать профиль окупившимся. Сутки чередуются 5/5 —
    /// детерминированный синтетический ряд, без монеты, seed только у
    /// самих реплик (A2).
    #[test]
    fn naive_product_flips_sign_when_net_component_lower_bound_is_negative() {
        const N_TOTAL: u64 = 100;
        const N_FILLED_PLUS: u64 = 80;
        const N_FILLED_MINUS: u64 = 20;
        const NET_PLUS: f64 = 8.0;
        const NET_MINUS: f64 = -6.0;
        const DAYS: i64 = 10;
        const ALPHA: f64 = 0.05;
        const REPLICATIONS: u32 = 999;
        let regime = |day: i64| day % 2 == 0;

        let observations = regime_observations(
            DAYS,
            N_TOTAL,
            NET_PLUS,
            N_FILLED_PLUS,
            NET_MINUS,
            N_FILLED_MINUS,
            regime,
        );
        let interval = net_fill_interval(&observations, ALPHA, REPLICATIONS, 7)
            .expect("оба режима дают исполнения");
        assert!(
            close(interval.point_bps, 2.6),
            "точка — та же замкнутая форма"
        );

        let day_sums = regime_day_sums(
            DAYS,
            N_TOTAL,
            NET_PLUS,
            N_FILLED_PLUS,
            NET_MINUS,
            N_FILLED_MINUS,
            regime,
        );
        let total_n_filled: u64 = day_sums.iter().map(|d| d.1).sum();
        let a_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.0, d.1)).collect();
        let b_series: Vec<(f64, u64)> = day_sums.iter().map(|d| (d.2, d.3)).collect();
        let naive_a = naive_series_lower(&a_series, total_n_filled, ALPHA, REPLICATIONS, 8);
        let naive_b = naive_series_lower(
            &b_series,
            u64::try_from(DAYS).unwrap_or(0) * N_TOTAL,
            ALPHA,
            REPLICATIONS,
            9,
        );
        let naive_lower = naive_a * naive_b;

        assert!(
            naive_a < 0.0,
            "триггер критерия: нижняя граница net-компоненты отрицательна, получено {naive_a}"
        );
        assert!(
            naive_lower > 0.0,
            "бессмыслица наивного метода: произведение двух неуверенных границ \
             {naive_a} и {naive_b} обязано перевернуть знак в плюс, получено {naive_lower}"
        );
        assert!(
            interval.lower_bps <= 0.0,
            "совместный метод обязан остаться корректным (не признавать окупаемость): {}",
            interval.lower_bps
        );
        assert!(
            interval.lower_bps < naive_lower,
            "совместная граница {} обязана быть заметно ниже бессмысленной наивной {naive_lower}",
            interval.lower_bps
        );
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
