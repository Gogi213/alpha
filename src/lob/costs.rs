//! Издержки и проскальзывание по Decision 15 (план, §5.3).
//!
//! Вход — мейкер у своей стороны спреда без улучшения (тип ордера —
//! Decision 19); выход — тейкер с издержкой в один тик против позиции плюс
//! половина наблюдённого спреда в момент выхода. Круг комиссий —
//! по ногам: мейкер 0.014 % / тейкер 0.035 % минус 10 % возврата (В-63, условия счёта
//! владельца 2026-09-18; прежнее H4 — публичный non-VIP 0.02 % / 0.055 %).
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

/// Комиссия мейкера, bps: 0.014 % — условия счёта владельца (скриншот
/// account.tiger.com, 2026-09-18, В-63). Прежнее H4 — публичный non-VIP 0.02 %.
pub const MAKER_FEE_BPS: f64 = 1.4;

/// Комиссия тейкера, bps: 0.035 % (там же). Прежнее H4 — 0.055 %.
pub const TAKER_FEE_BPS: f64 = 3.5;

/// Возврат комиссий владельцу: 10 % от уплаченного (там же, «вы получаете 10 %
/// вознаграждения от торговых комиссий»). Применяется к каждой ноге.
pub const FEE_REBATE_SHARE: f64 = 0.10;

/// Эффективная комиссия одной ноги после возврата, bps: мейкер 1.26, тейкер 3.15.
pub fn leg_fee_bps(taker: bool) -> f64 {
    (if taker { TAKER_FEE_BPS } else { MAKER_FEE_BPS }) * (1.0 - FEE_REBATE_SHARE)
}

/// Круг мейкер-тейкер после возврата, bps (Decision 19 → В-63): одно число
/// для markout-метрик, у которых ног нет (G0, G3, дашборд, отбор, пол тейка);
/// круг **сделки** считается по ногам — `backtest::roundtrip_net_bps`.
pub const ROUNDTRIP_FEES_BPS: f64 = (MAKER_FEE_BPS + TAKER_FEE_BPS) * (1.0 - FEE_REBATE_SHARE);

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
mod tests;
