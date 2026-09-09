//! `hftbacktest` со стратегией Decision 20, RTT из 6.4, очередь
//! `RiskAdverseQueueModel` (план, §6.3).
//!
//! Стратегия предрегистрирована целиком (Decision 20): триггер — срабатывание
//! условия ячейки, сторона по σ (Decision 14), вход мейкером у своей стороны
//! спреда с временем жизни ордера 2 с, выход тейкером ровно на `t₀ + 10 с`
//! без стопа и без цели, одна позиция за раз, модель очереди —
//! `RiskAdverseQueueModel`. Шаг гоняется по обеим ячейкам, вердикт выносит C2,
//! если она прошла G2, иначе C1 (тот же выбор, что `costs`, — потребитель,
//! своей копии правила здесь нет).
//!
//! # Одна стратегия на бэктест и живую торговлю (A6)
//!
//! Ядро — свободная функция поверх трейта `Bot<MD>` крейта: тот же код идёт
//! и в `Backtest`, и в живую сторону без правок. Поэтому здесь только то, что
//! есть на обеих сторонах: время — `Bot::current_timestamp`, продвижение —
//! общий метод продвижения (действует и там, и там), ордера — лимитные
//! (`GTX` на вход, пересекающий `GTC` на выход: `NoPartialFillExchange`
//! другого не принимает, а рынок как тип живая сторона не обязана уметь).
//! Зависимость `live` крейта в этом репозитории не включена, так что живая
//! сторона проверяется совпадением интерфейса, а не инстанцированием.
//!
//! # RTT из 6.4
//!
//! Замер 6.4 печатает две метки раздельно: приём ответным кадром и исполнение
//! приватным стримом. В гейт идёт та, что соответствует событию, на которое
//! реагирует модель очереди; для `RiskAdverseQueueModel` это исполнение
//! (позиция двигается по сделкам на цене). Измеренная RTT кладётся целиком на
//! входное плечо (`response = 0`): момент прихода подтверждения совпадает с
//! измеренным, а вступление в очередь — самое позднее, то есть допущение
//! консервативное. Делить RTT пополам было бы изобретённым числом.
//! См. `latency_from_rtt`.
//!
//! # Done-condition 6.3 буквально
//!
//! Отчёт несёт: PnL-кривую, число заполнений, **число пропущенных сигналов
//! отдельно по причине — ордер не исполнился за 2 с и позиция уже открыта** —
//! и сравнение с оценкой 5.3. Живой прогон и недельные данные в песочнице
//! невозможны, поэтому PnL-кривая — чистая функция от входов, тестируемая на
//! синтетике без недели.
//!
//! Модуль не хранит состояния и не выделяет память вне отчёта и очереди
//! самого крейта.

use hftbacktest::depth::{MarketDepth, INVALID_MAX, INVALID_MIN};
use hftbacktest::types::{Bot, ElapseResult, OrdType, Side as HbtSide, Status, TimeInForce};

use crate::lob::costs::{select_verdict_cell, VerdictCell, ROUNDTRIP_FEES_BPS};

// ---------------------------------------------------------------------------
// Константы Decision 20. Каждое число — из плана.
// ---------------------------------------------------------------------------

/// Время жизни входного мейкер-ордера: 2 с в наносекундах. Не исполнился —
/// снимается, сигнал считается пропущенным (`MissReason::EntryTimeout`).
pub const ENTRY_TTL_NS: i64 = 2_000_000_000;

/// Горизонт позиции: выход тейкером ровно на `t₀ + 10 с`, без стопа и цели.
/// Тот же горизонт, что у ячеек C1/C2: меряется markout, выход совпадает.
pub const HOLD_NS: i64 = 10_000_000_000;

/// σ уровня на стороне бида (Decision 14): подразумевает шорт.
pub const SIGMA_SHORT: i8 = -1;

/// σ уровня на стороне аска (Decision 14): подразумевает лонг.
pub const SIGMA_LONG: i8 = 1;

// ---------------------------------------------------------------------------
// Вход: сигналы триггера ячеек.
// ---------------------------------------------------------------------------

/// Один сигнал стратегии: срабатывание условия ячейки в момент `t₀`.
/// Принадлежность к C1/C2 решает вызывающий (`watch::is_c1`/`is_c2`,
/// потребитель); сюда сигнал приходит уже отобранным, вместе со стороной
/// σ по Decision 14.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Signal {
    /// Момент срабатывания `t₀` в наносекундах (часы стороны, не стенка).
    pub t0_ns: i64,
    /// Сторона по σ: `-1` — бид (шорт), `+1` — аск (лонг).
    pub sigma: i8,
}

// ---------------------------------------------------------------------------
// Пропуски: обязательная колонка, раздельно по причине.
// ---------------------------------------------------------------------------

/// Причина пропуска сигнала. Третьей причины нет: сигнал либо застаёт
/// занятую позицию, либо вход не исполняется за 2 с.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MissReason {
    /// Входной ордер не исполнился за 2 с и снят.
    EntryTimeout,
    /// Сигнал пришёл при открытой позиции: в очередь не ставится.
    PositionBusy,
}

/// Счётчик пропусков с раздельными колонками. Done-condition требует печатать
/// обе: доля исполнившихся входов — разница между симуляцией и реальностью.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct MissLedger {
    /// Пропущено: ордер не исполнился за 2 с.
    pub timeout: u64,
    /// Пропущено: позиция уже открыта.
    pub busy: u64,
}

impl MissLedger {
    /// Учесть один пропуск по его причине.
    pub fn record(&mut self, reason: MissReason) {
        match reason {
            MissReason::EntryTimeout => self.timeout = self.timeout.saturating_add(1),
            MissReason::PositionBusy => self.busy = self.busy.saturating_add(1),
        }
    }

    /// Всего пропусков обеими причинами.
    pub fn total(self) -> u64 {
        self.timeout.saturating_add(self.busy)
    }

    /// Обязательная колонка пропусков: обе причины раздельно.
    pub fn format_column(self) -> String {
        format!("missed_timeout={} missed_busy={}", self.timeout, self.busy)
    }
}

// ---------------------------------------------------------------------------
// Заполнение и PnL-кривая как чистые функции от входов.
// ---------------------------------------------------------------------------

/// Один закрытый круг: направление и обе ноги исполнения.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fill {
    /// Направление: `+1` — лонг (покупка, затем продажа),
    /// `-1` — шорт (продажа, затем покупка).
    pub dir: i8,
    /// Цена входа (исполнение мейкера).
    pub entry_px: f64,
    /// Цена выхода (исполнение тейкера).
    pub exit_px: f64,
    /// Размер круга. В отчёт идёт как есть; в bps сокращается.
    pub qty: f64,
}

/// Чистый результат круга в bps: направленная доходность минус круг
/// мейкер-тейкер 7.5 bps (Decision 19, потребитель `costs`). `None` при
/// неположительном входе или неконечных ценах: отсутствие данных не есть
/// нулевой результат (то же правило, что неконечный markout в 5.2).
pub fn roundtrip_net_bps(fill: &Fill) -> Option<f64> {
    if !fill.entry_px.is_finite() || !fill.exit_px.is_finite() || fill.entry_px <= 0.0 {
        return None;
    }
    let dir = match fill.dir {
        1 => 1.0,
        -1 => -1.0,
        _ => return None,
    };
    let gross = dir * (fill.exit_px - fill.entry_px) / fill.entry_px * 10_000.0;
    Some(gross - ROUNDTRIP_FEES_BPS)
}

/// PnL-кривая: накопленный чистый результат по кругам в порядке исполнения.
/// Чистая функция от входов — тестируется без недели. `None`, если кругов
/// нет или хотя бы один не посчитался: молча выкидывать его значило бы
/// пересчитывать выборку задним числом (то же правило, что `mean_net_bps`).
pub fn pnl_curve_bps(fills: &[Fill]) -> Option<Vec<f64>> {
    if fills.is_empty() {
        return None;
    }
    let mut curve = Vec::with_capacity(fills.len());
    let mut running = 0.0;
    for f in fills {
        running += roundtrip_net_bps(f)?;
        curve.push(running);
    }
    Some(curve)
}

/// Средний чистый результат круга в bps. `None` — кругов нет или хотя бы
/// один не посчитался (см. `pnl_curve_bps`).
pub fn mean_net_bps(fills: &[Fill]) -> Option<f64> {
    let curve = pnl_curve_bps(fills)?;
    curve.last().map(|last| last / fills.len() as f64)
}

// ---------------------------------------------------------------------------
// Сравнение с оценкой 5.3 и гейт G4.
// ---------------------------------------------------------------------------

/// Сравнение реализованного среднего с оценкой шага 5.3 (колонка «после
/// издержек» той же вердиктной ячейки, потребитель `costs::mean_net_bps`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Comparison {
    /// Средний net оценки 5.3 в bps (`None` — оценка не посчиталась).
    pub mean_net_53_bps: Option<f64>,
    /// Реализованный средний net бэктеста в bps (`None` — кругов нет).
    pub realized_mean_net_bps: Option<f64>,
    /// Разность `реализация − оценка` в bps (`None` — нет любого из входов).
    pub diff_bps: Option<f64>,
}

/// Разность реализации и оценки 5.3. Отказ любого входа — `None` разности,
// а не ноль: совпадение с оценкой надо показать, а не предположить.
pub fn compare_with_53(
    mean_net_53_bps: Option<f64>,
    realized_mean_net_bps: Option<f64>,
) -> Comparison {
    let diff_bps = match (mean_net_53_bps, realized_mean_net_bps) {
        (Some(a), Some(b)) if a.is_finite() && b.is_finite() => Some(b - a),
        _ => None,
    };
    Comparison {
        mean_net_53_bps,
        realized_mean_net_bps,
        diff_bps,
    }
}

impl Comparison {
    /// Строка сравнения для отчёта: оба числа и разность.
    pub fn format_line(self) -> String {
        format!(
            "vs_5.3: estimate={} realized={} diff={}",
            fmt_opt(self.mean_net_53_bps),
            fmt_opt(self.realized_mean_net_bps),
            fmt_opt(self.diff_bps)
        )
    }
}

/// Вердикт гейта G4. Чистый PnL положителен при медианном RTT и остаётся
/// положительным при 95-м перцентиле — иначе красный.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum G4Verdict {
    /// Положителен на обеих задержках.
    Pass,
    /// Иначе красный (включая отсутствие данных).
    Red,
}

impl G4Verdict {
    /// Проход гейта.
    pub fn is_pass(self) -> bool {
        matches!(self, G4Verdict::Pass)
    }
}

impl std::fmt::Display for G4Verdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            G4Verdict::Pass => write!(f, "G4 pass: net>0 at median and p95 RTT"),
            G4Verdict::Red => write!(f, "G4 RED"),
        }
    }
}

/// Решение G4 строго по формулировке: оба средних конечны и строго
/// положительны — проход, иначе (включая `None`) красный.
pub fn decide_g4(median_net_bps: Option<f64>, p95_net_bps: Option<f64>) -> G4Verdict {
    match (median_net_bps, p95_net_bps) {
        (Some(m), Some(p)) if m.is_finite() && m > 0.0 && p.is_finite() && p > 0.0 => {
            G4Verdict::Pass
        }
        _ => G4Verdict::Red,
    }
}

// ---------------------------------------------------------------------------
// RTT 6.4 → задержка крейта.
// ---------------------------------------------------------------------------

/// Отображение измеренной RTT исполнения (метка приватного стрима 6.4) на
/// модель задержки крейта `(entry, response)`. Вся RTT — на входное плечо:
/// подтверждение приходит в измеренный момент, а в очередь ордер встаёт
/// максимально поздно (консервативно; делить пополам было бы изобретённым
/// числом). Отрицательная RTT невозможна — возвращается `(0, 0)` как отказ
/// насыщением, а не паника.
pub fn latency_from_rtt(exec_rtt_ns: i64) -> (i64, i64) {
    if exec_rtt_ns <= 0 {
        return (0, 0);
    }
    (exec_rtt_ns, 0)
}

// ---------------------------------------------------------------------------
// Сторона и цены по σ (Decision 14 + Decision 19).
// ---------------------------------------------------------------------------

/// Сторона входа по σ: бид (`-1`) подразумевает шорт, аск (`+1`) — лонг
/// (Decision 14 делает предсказанный знак положительным во всех ячейках).
/// `None` — σ не из пары: такой сигнал не торгуется, а отбрасывается
/// вызывающим до драйвера.
pub fn entry_side(sigma: i8) -> Option<HbtSide> {
    match sigma {
        SIGMA_SHORT => Some(HbtSide::Sell),
        SIGMA_LONG => Some(HbtSide::Buy),
        _ => None,
    }
}

/// Цена входа Decision 19 — мейкер у своей стороны спреда: шорт выставляется
/// на лучший аск, лонг на лучший бид. `None` — книга неполна: на incomplete
/// book не торгуется ни одна сторона, обе цены обязаны быть конечными.
pub fn entry_price(side: HbtSide, best_bid: f64, best_ask: f64) -> Option<f64> {
    if !(best_bid.is_finite() && best_ask.is_finite()) {
        return None;
    }
    match side {
        HbtSide::Buy => Some(best_bid),
        HbtSide::Sell => Some(best_ask),
        HbtSide::None | HbtSide::Unsupported => None,
    }
}

/// Цена выхода — тейкер пересекающим лимитом: лонг продаёт по лучшему биду,
/// шорт покупает по лучшему аску. Цена равна лучшей на своей стороне, потому
/// исполнение идёт по лучшей (модель это гарантирует). `None` — книга
/// неполна (то же правило, что у входа).
pub fn exit_price(side: HbtSide, best_bid: f64, best_ask: f64) -> Option<f64> {
    if !(best_bid.is_finite() && best_ask.is_finite()) {
        return None;
    }
    match side {
        HbtSide::Buy => Some(best_bid),
        HbtSide::Sell => Some(best_ask),
        HbtSide::None | HbtSide::Unsupported => None,
    }
}

/// Печать опционального числа: `none`, если данных нет.
fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) if x.is_finite() => format!("{x:.4}"),
        Some(_) => "nonfinite".to_string(),
        None => "none".to_string(),
    }
}

// ---------------------------------------------------------------------------
// Прогон одной ячейки поверх `Bot<MD>`.
// ---------------------------------------------------------------------------

/// Настройки прогона ячейки.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DriveConfig {
    /// Размер круга. План: минимальный лот биржи (Decision 22).
    pub order_qty: f64,
    /// Первый идентификатор ордеров; дальше — по порядку, каждый ордер
    /// уникален (требование трейта).
    pub first_order_id: u64,
}

/// Итог прогона одной ячейки: done-condition 6.3 буквально — заполнения,
/// пропуски раздельно по причине, кривая из `pnl_curve_bps`.
#[derive(Debug, Clone, PartialEq)]
pub struct CellBacktest {
    /// Ячейка прогона (C1 или C2).
    pub cell: VerdictCell,
    /// Сигналов подано в драйвер.
    pub signals: u64,
    /// Закрытые круги в порядке исполнения.
    pub fills: Vec<Fill>,
    /// Пропуски раздельно по причине (обязательная колонка).
    pub misses: MissLedger,
    /// Данные кончились раньше, чем позиция закрылась или время вышло:
    /// итог неполон, вердикт по нему не выносится.
    pub incomplete: bool,
}

impl CellBacktest {
    /// Число заполнений (закрытых кругов).
    pub fn n_fills(&self) -> usize {
        self.fills.len()
    }

    /// Средний чистый результат круга в bps.
    pub fn mean_net_bps(&self) -> Option<f64> {
        mean_net_bps(&self.fills)
    }

    /// Строки ячейки для отчёта: заполнения, обе колонки пропусков, кривая
    /// компактно (длина, финал, минимум) и полно — через `fills`.
    pub fn summary_lines(&self, name: &str) -> Vec<String> {
        let mut lines = Vec::with_capacity(4);
        lines.push(format!(
            "{name}: signals={} fills={} {} incomplete={}",
            self.signals,
            self.fills.len(),
            self.misses.format_column(),
            self.incomplete
        ));
        match pnl_curve_bps(&self.fills) {
            Some(curve) => {
                let last = curve[curve.len() - 1];
                let min = curve.iter().fold(f64::INFINITY, |a, b| a.min(*b));
                lines.push(format!(
                    "{name} pnl_bps: n={} final={:.4} min={:.4}",
                    curve.len(),
                    last,
                    min
                ));
            }
            None => lines.push(format!("{name} pnl_bps: none (no fills)")),
        }
        lines.push(format!(
            "{name} mean_net_bps={}",
            fmt_opt(self.mean_net_bps())
        ));
        lines
    }
}

/// Итог шага 6.3: обе ячейки, вердиктная по Decision 20, сравнение с 5.3
/// по вердиктной ячейке и вердикт G4.
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestReport {
    /// Прогон C1.
    pub c1: CellBacktest,
    /// Прогон C2.
    pub c2: CellBacktest,
    /// Вердиктная ячейка: C2, если прошла G2, иначе C1.
    pub verdict_cell: VerdictCell,
    /// Сравнение с оценкой 5.3 вердиктной ячейки.
    pub comparison: Comparison,
    /// Вердикт гейта G4.
    pub g4: G4Verdict,
}

impl BacktestReport {
    /// Строки отчёта по done-condition: обе ячейки с колонками пропусков,
    /// вердиктная ячейка, сравнение с 5.3, вердикт G4.
    pub fn summary_lines(&self) -> Vec<String> {
        let mut lines = Vec::with_capacity(9);
        lines.extend(self.c1.summary_lines("C1"));
        lines.extend(self.c2.summary_lines("C2"));
        lines.push(format!("verdict_cell={}", self.verdict_cell));
        lines.push(self.comparison.format_line());
        lines.push(self.g4.to_string());
        lines
    }
}

/// Собрать отчёт из двух готовых прогонов: выбор вердиктной ячейки по флагу
/// прохода C2 (потребитель `costs`), сравнение читает средний net именно её.
pub fn assemble_report(
    c1: CellBacktest,
    c2: CellBacktest,
    c2_passed: bool,
    mean_net_53_c1: Option<f64>,
    mean_net_53_c2: Option<f64>,
    median_net_bps: Option<f64>,
    p95_net_bps: Option<f64>,
) -> BacktestReport {
    let verdict_cell = select_verdict_cell(c2_passed);
    let (realized, estimate) = match verdict_cell {
        VerdictCell::C1 => (c1.mean_net_bps(), mean_net_53_c1),
        VerdictCell::C2 => (c2.mean_net_bps(), mean_net_53_c2),
    };
    BacktestReport {
        c1,
        c2,
        verdict_cell,
        comparison: compare_with_53(estimate, realized),
        g4: decide_g4(median_net_bps, p95_net_bps),
    }
}

/// Лучшие цены стороны как `(bid, ask)`; `None` — книга неполна (нет лучшей
/// с любой стороны или цены не конечны).
fn best_prices<MD: MarketDepth>(depth: &MD) -> Option<(f64, f64)> {
    if depth.best_bid_tick() == INVALID_MIN || depth.best_ask_tick() == INVALID_MAX {
        return None;
    }
    let (bid, ask) = (depth.best_bid(), depth.best_ask());
    if bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0 {
        Some((bid, ask))
    } else {
        None
    }
}

/// Прогон стратегии Decision 20 по сигналам одной ячейки поверх любого
/// `Bot<MD>` — бэктестера или живой стороны (A6: тот же код без правок).
/// Очередь и задержки — внутри стороны (в бэктесте: `RiskAdverseQueueModel`
/// и задержка из `latency_from_rtt`); драйвер их не касается, он только
/// ставит мейкер на 2 с, снимает неисполнившийся и выходит тейкером ровно
/// на `t₀ + 10 с`. Одна позиция за раз: сигналы при открытой позиции
/// считаются пропущенными (`PositionBusy`), в очередь не ставятся.
pub fn drive_cell<B, MD>(
    bot: &mut B,
    asset_no: usize,
    cell: VerdictCell,
    signals: &[Signal],
    cfg: &DriveConfig,
) -> Result<CellBacktest, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut order: Vec<Signal> = signals.to_vec();
    order.sort_by_key(|s| s.t0_ns);
    let mut fills: Vec<Fill> = Vec::new();
    let mut misses = MissLedger::default();
    let mut next_id = cfg.first_order_id;
    let mut incomplete = false;
    // Момент закрытия текущей/последней позиции. Драйвер ведёт круги
    // последовательно, поэтому сигнал с t₀ раньше этого момента пришёл при
    // открытой позиции, даже если к разбору она уже закрыта: пропуск по
    // занятости, а не новый вход задним числом.
    let mut blocked_until_ns: i64 = i64::MIN;

    // Инициализация часов стороны: свежий бэктестер стоит на максимуме до
    // первого продвижения, живая сторона — на стенке; нулевой шаг первую
    // ставит на первый фид, второй безвреден.
    if bot.elapse(0)? == ElapseResult::EndOfData {
        return Ok(CellBacktest {
            cell,
            signals: order.len() as u64,
            fills,
            misses,
            incomplete: true,
        });
    }

    for sig in &order {
        // Неизвестная σ — не сигнал стратегии: пропускается без счёта, как
        // чужой символ в счётчике `watch` (молчание здесь — фильтр, а не
        // потеря: выборка ячеек его не содержит по построению).
        let side = match entry_side(sig.sigma) {
            Some(s) => s,
            None => continue,
        };
        // Догнать время сигнала часами стороны.
        let now = bot.current_timestamp();
        if sig.t0_ns > now && bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
            incomplete = true;
            break;
        }
        // Одна позиция за раз: сигнал внутри открытой позиции — пропуск,
        // не очередь. Проверка двойная: по моменту закрытия (сигнал пришёл
        // раньше, чем позиция закрылась) и по факту (позиция открыта извне).
        if sig.t0_ns < blocked_until_ns || bot.position(asset_no) != 0.0 {
            misses.record(MissReason::PositionBusy);
            continue;
        }
        // Цена входа Decision 19; без книги вход невозможен — экономически
        // то же, что неисполнившийся ордер.
        let entry_px =
            best_prices(bot.depth(asset_no)).and_then(|(bid, ask)| entry_price(side, bid, ask));
        let Some(entry_px) = entry_px else {
            misses.record(MissReason::EntryTimeout);
            continue;
        };
        let entry_id = next_id;
        next_id = next_id.saturating_add(1);
        match side {
            HbtSide::Buy => {
                bot.submit_buy_order(
                    asset_no,
                    entry_id,
                    entry_px,
                    cfg.order_qty,
                    TimeInForce::GTX,
                    OrdType::Limit,
                    false,
                )?;
            }
            HbtSide::Sell => {
                bot.submit_sell_order(
                    asset_no,
                    entry_id,
                    entry_px,
                    cfg.order_qty,
                    TimeInForce::GTX,
                    OrdType::Limit,
                    false,
                )?;
            }
            HbtSide::None | HbtSide::Unsupported => continue,
        }
        // Ждать исполнения до конца жизни ордера.
        let deadline = sig.t0_ns.saturating_add(ENTRY_TTL_NS);
        let now = bot.current_timestamp();
        if deadline > now && bot.elapse(deadline - now)? == ElapseResult::EndOfData {
            incomplete = true;
            break;
        }
        let entered_px = bot
            .orders(asset_no)
            .get(&entry_id)
            .filter(|o| o.status == Status::Filled)
            .map(|o| o.exec_price());
        let Some(entered_px) = entered_px else {
            // Не исполнился за 2 с — снять и посчитать пропуском.
            let _ = bot.cancel(asset_no, entry_id, false);
            bot.clear_inactive_orders(Some(asset_no));
            misses.record(MissReason::EntryTimeout);
            continue;
        };
        // Выход тейкером ровно на t₀ + 10 с, без стопа и без цели.
        let exit_at = sig.t0_ns.saturating_add(HOLD_NS);
        // Позиция открыта исполнившимся входом и закроется на выходе:
        // сигналы внутри — пропуски по занятости.
        blocked_until_ns = exit_at;
        let now = bot.current_timestamp();
        if exit_at > now && bot.elapse(exit_at - now)? == ElapseResult::EndOfData {
            incomplete = true;
            break;
        }
        let exit_px =
            best_prices(bot.depth(asset_no)).and_then(|(bid, ask)| exit_price(side, bid, ask));
        let Some(exit_want) = exit_px else {
            incomplete = true;
            break;
        };
        let exit_id = next_id;
        next_id = next_id.saturating_add(1);
        // Пересекающий лимит GTC: исполнение по лучшей как тейкер.
        match side {
            HbtSide::Buy => {
                bot.submit_sell_order(
                    asset_no,
                    exit_id,
                    exit_want,
                    cfg.order_qty,
                    TimeInForce::GTC,
                    OrdType::Limit,
                    false,
                )?;
            }
            HbtSide::Sell => {
                bot.submit_buy_order(
                    asset_no,
                    exit_id,
                    exit_want,
                    cfg.order_qty,
                    TimeInForce::GTC,
                    OrdType::Limit,
                    false,
                )?;
            }
            HbtSide::None | HbtSide::Unsupported => continue,
        }
        // Дать ответу дойти: ждём ответ именно по этому ордеру, а не
        // фиксированный шаг (шаг убегал бы за конец фида; потолок ниже —
        // только страховка против немого фида).
        if bot.wait_order_response(asset_no, exit_id, EXIT_RESP_TIMEOUT_NS)?
            == ElapseResult::EndOfData
        {
            incomplete = true;
            break;
        }
        let exited_px = bot
            .orders(asset_no)
            .get(&exit_id)
            .filter(|o| o.status == Status::Filled)
            .map(|o| o.exec_price());
        match exited_px {
            Some(exit_px) => fills.push(Fill {
                dir: if side == HbtSide::Buy { 1 } else { -1 },
                entry_px: entered_px,
                exit_px,
                qty: cfg.order_qty,
            }),
            None => {
                incomplete = true;
                break;
            }
        }
        bot.clear_inactive_orders(Some(asset_no));
    }
    bot.clear_inactive_orders(Some(asset_no));

    Ok(CellBacktest {
        cell,
        signals: order.len() as u64,
        fills,
        misses,
        incomplete,
    })
}

/// Страховочный потолок ожидания ответа на выход: ответ приходит за плечо
/// задержки стороны, потолок нужен только против немого фида. Не модельная
/// величина и не параметр стратегии — на PnL не влияет.
const EXIT_RESP_TIMEOUT_NS: i64 = 30_000_000_000;

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// Предрегистрация буквально: вход мейкером живёт 2 с, выход ровно
    /// на t₀ + 10 с. Числа — константы, а не параметры вызова.
    #[test]
    fn decision20_timing_is_frozen_in_constants() {
        assert_eq!(ENTRY_TTL_NS, 2_000_000_000);
        assert_eq!(HOLD_NS, 10_000_000_000);
        const {
            assert!(HOLD_NS > ENTRY_TTL_NS, "выход обязан быть позже снятия");
        }
    }

    /// Сторона по σ (Decision 14): бид — шорт, аск — лонг. Зеркальная пара
    /// при зеркальных книгах даёт зеркальные цены входа.
    #[test]
    fn side_follows_sigma_and_entry_rests_on_own_side() {
        assert_eq!(entry_side(SIGMA_SHORT), Some(HbtSide::Sell));
        assert_eq!(entry_side(SIGMA_LONG), Some(HbtSide::Buy));
        assert_eq!(entry_side(0), None);
        // Шорт — на лучший аск, лонг — на лучший бид (Decision 19).
        assert_eq!(entry_price(HbtSide::Sell, 100.0, 101.0), Some(101.0));
        assert_eq!(entry_price(HbtSide::Buy, 100.0, 101.0), Some(100.0));
        // Зеркало: перемена сторон книги меняет цены местами.
        assert_eq!(
            entry_price(HbtSide::Sell, 100.0, 101.0),
            entry_price(HbtSide::Buy, 101.0, 100.0)
        );
        assert_eq!(entry_price(HbtSide::Buy, f64::NAN, 101.0), None);
    }

    /// Выход — тейкер пересекающим лимитом: лонг по биду, шорт по аску.
    #[test]
    fn exit_takes_on_own_side_book() {
        assert_eq!(exit_price(HbtSide::Buy, 100.0, 101.0), Some(100.0));
        assert_eq!(exit_price(HbtSide::Sell, 100.0, 101.0), Some(101.0));
        assert_eq!(exit_price(HbtSide::Buy, 100.0, f64::NAN), None);
    }

    /// Формула круга буквально: направленный возврат минус 7.5 bps круга.
    /// Лонг 100 → 101: +100 bps валом, 92.5 чистыми.
    #[test]
    fn roundtrip_formula_is_exact_on_synthetic_fill() {
        let long = Fill {
            dir: 1,
            entry_px: 100.0,
            exit_px: 101.0,
            qty: 1.0,
        };
        assert!(close(roundtrip_net_bps(&long).unwrap(), 92.5));
        let short = Fill {
            dir: -1,
            entry_px: 101.0,
            exit_px: 100.0,
            qty: 1.0,
        };
        // Зеркало в уровнях не симметрично в bps: база другая (101, не 100).
        assert!(close(
            roundtrip_net_bps(&short).unwrap(),
            10_000.0 / 101.0 - 7.5
        ));
        let loss = Fill {
            dir: 1,
            entry_px: 100.0,
            exit_px: 99.0,
            qty: 1.0,
        };
        assert!(close(roundtrip_net_bps(&loss).unwrap(), -107.5));
        assert_eq!(
            roundtrip_net_bps(&Fill {
                dir: 1,
                entry_px: 0.0,
                exit_px: 101.0,
                qty: 1.0
            }),
            None
        );
        assert_eq!(
            roundtrip_net_bps(&Fill {
                dir: 0,
                entry_px: 100.0,
                exit_px: 101.0,
                qty: 1.0
            }),
            None
        );
        assert_eq!(
            roundtrip_net_bps(&Fill {
                dir: 1,
                entry_px: f64::NAN,
                exit_px: 101.0,
                qty: 1.0
            }),
            None
        );
    }

    /// PnL-кривая как функция от входов: накопление по шагам, без недели.
    #[test]
    fn pnl_curve_accumulates_per_fill_without_a_week() {
        let fills = [
            Fill {
                dir: 1,
                entry_px: 100.0,
                exit_px: 101.0,
                qty: 1.0,
            },
            Fill {
                dir: 1,
                entry_px: 100.0,
                exit_px: 99.0,
                qty: 1.0,
            },
        ];
        let curve = pnl_curve_bps(&fills).unwrap();
        assert_eq!(curve.len(), 2);
        assert!(close(curve[0], 92.5));
        assert!(close(curve[1], 92.5 - 107.5));
        assert!(close(mean_net_bps(&fills).unwrap(), (92.5 - 107.5) / 2.0));
        assert_eq!(pnl_curve_bps(&[]), None, "пусто — нет кривой, а не ноль");
        assert_eq!(mean_net_bps(&[]), None);
        let bad = [
            fills[0],
            Fill {
                dir: 1,
                entry_px: 0.0,
                exit_px: 1.0,
                qty: 1.0,
            },
        ];
        assert_eq!(
            pnl_curve_bps(&bad),
            None,
            "битый круг травит кривую, а не выкидывается молча"
        );
    }

    /// Обязательная колонка пропусков: причины считаются раздельно и
    /// печатаются обе, даже нулевые.
    #[test]
    fn miss_columns_are_split_by_reason() {
        let mut ledger = MissLedger::default();
        ledger.record(MissReason::EntryTimeout);
        ledger.record(MissReason::EntryTimeout);
        ledger.record(MissReason::PositionBusy);
        assert_eq!(ledger.timeout, 2);
        assert_eq!(ledger.busy, 1);
        assert_eq!(ledger.total(), 3);
        let col = ledger.format_column();
        assert!(col.contains("missed_timeout=2"), "колонка обязана: {col}");
        assert!(col.contains("missed_busy=1"), "колонка обязана: {col}");
        assert!(
            MissLedger::default()
                .format_column()
                .contains("missed_timeout=0"),
            "нулевая колонка тоже печатается"
        );
    }

    /// Сравнение с оценкой 5.3: разность реализации и оценки, отказы — None.
    #[test]
    fn comparison_is_realized_minus_estimate() {
        let c = compare_with_53(Some(10.0), Some(92.5));
        assert!(close(c.diff_bps.unwrap(), 82.5));
        assert_eq!(compare_with_53(None, Some(1.0)).diff_bps, None);
        assert_eq!(compare_with_53(Some(1.0), None).diff_bps, None);
        assert_eq!(compare_with_53(Some(f64::NAN), Some(1.0)).diff_bps, None);
        let line = c.format_line();
        assert!(line.contains("vs_5.3"), "строка обязана называться: {line}");
        assert!(line.contains("diff=82.5000"), "разность на месте: {line}");
    }

    /// Гейт G4 буквально: положителен при медиане и остаётся положительным
    /// при p95 — проход; иначе (включая отсутствие данных) красный.
    #[test]
    fn g4_passes_only_when_positive_at_both_latencies() {
        assert!(decide_g4(Some(1.0), Some(0.5)).is_pass());
        assert!(!decide_g4(Some(1.0), Some(-0.5)).is_pass());
        assert!(!decide_g4(Some(-1.0), Some(1.0)).is_pass());
        assert!(!decide_g4(Some(0.0), Some(1.0)).is_pass());
        assert!(!decide_g4(None, Some(1.0)).is_pass());
        assert!(!decide_g4(Some(1.0), None).is_pass());
        assert!(!decide_g4(Some(f64::NAN), Some(1.0)).is_pass());
        assert_eq!(decide_g4(Some(0.1), Some(0.1)), G4Verdict::Pass);
        assert_eq!(decide_g4(Some(0.1), Some(0.0)), G4Verdict::Red);
    }

    /// RTT 6.4 → задержка: вся измеренная RTT на входном плече, ответ — ноль.
    /// Момент подтверждения совпадает с измеренным, очередь — консервативно.
    #[test]
    fn rtt_maps_wholly_onto_entry_leg() {
        assert_eq!(latency_from_rtt(1_500_000), (1_500_000, 0));
        assert_eq!(latency_from_rtt(0), (0, 0));
        assert_eq!(latency_from_rtt(-5), (0, 0));
    }

    /// Вердиктная ячейка — потребитель `costs`: C2, если прошла G2, иначе C1.
    /// Отчёт читает средний net и оценку 5.3 именно её.
    #[test]
    fn verdict_cell_comes_from_costs_not_a_copy() {
        let c1 = CellBacktest {
            cell: VerdictCell::C1,
            signals: 2,
            fills: vec![Fill {
                dir: 1,
                entry_px: 100.0,
                exit_px: 101.0,
                qty: 1.0,
            }],
            misses: MissLedger::default(),
            incomplete: false,
        };
        let c2 = CellBacktest {
            cell: VerdictCell::C2,
            signals: 1,
            fills: vec![],
            misses: MissLedger {
                timeout: 1,
                busy: 0,
            },
            incomplete: false,
        };
        // C2 прошла: вердиктная — C2, её пустой net тянет сравнение в None.
        let rep = assemble_report(
            c1.clone(),
            c2.clone(),
            true,
            Some(5.0),
            Some(7.0),
            Some(1.0),
            Some(0.5),
        );
        assert_eq!(rep.verdict_cell, VerdictCell::C2);
        assert_eq!(rep.comparison.realized_mean_net_bps, None);
        assert_eq!(rep.comparison.mean_net_53_bps, Some(7.0));
        assert!(rep.g4.is_pass());
        // C2 не прошла: читается C1.
        let rep = assemble_report(c1, c2, false, Some(5.0), Some(7.0), Some(1.0), Some(-0.5));
        assert_eq!(rep.verdict_cell, VerdictCell::C1);
        assert!(close(rep.comparison.realized_mean_net_bps.unwrap(), 92.5));
        assert!(close(rep.comparison.diff_bps.unwrap(), 87.5));
        assert!(!rep.g4.is_pass());
    }

    /// Done-condition читается в строках: заполнения, обе колонки пропусков,
    /// вердиктная ячейка, сравнение, G4.
    #[test]
    fn summary_carries_every_done_item() {
        let rep = assemble_report(
            CellBacktest {
                cell: VerdictCell::C1,
                signals: 3,
                fills: vec![Fill {
                    dir: 1,
                    entry_px: 100.0,
                    exit_px: 101.0,
                    qty: 1.0,
                }],
                misses: MissLedger {
                    timeout: 1,
                    busy: 1,
                },
                incomplete: false,
            },
            CellBacktest {
                cell: VerdictCell::C2,
                signals: 0,
                fills: vec![],
                misses: MissLedger::default(),
                incomplete: false,
            },
            false,
            Some(5.0),
            None,
            Some(2.0),
            Some(1.0),
        );
        let text = rep.summary_lines().join("\n");
        assert!(text.contains("fills=1"), "заполнения: {text}");
        assert!(
            text.contains("missed_timeout=1"),
            "колонка пропусков: {text}"
        );
        assert!(text.contains("missed_busy=1"), "колонка пропусков: {text}");
        assert!(text.contains("verdict_cell=C1"), "вердиктная: {text}");
        assert!(text.contains("vs_5.3"), "сравнение: {text}");
        assert!(text.contains("G4 pass"), "гейт: {text}");
        assert!(text.contains("pnl_bps"), "кривая: {text}");
    }

    // -----------------------------------------------------------------------
    // Очередь RiskAdverseQueueModel на фикстуре крейта: позиция двигается
    // только сделками на той же цене (самое консервативное допущение).
    // -----------------------------------------------------------------------

    /// Очередь встаёт за видимым объёмом, сделка на чужой цене её не двигает,
    /// сделка на своей — двигает, исполнение наступает при съеденной очереди.
    #[test]
    fn risk_adverse_queue_moves_only_on_same_price_trades() {
        use hftbacktest::backtest::models::{QueueModel, RiskAdverseQueueModel};
        use hftbacktest::depth::{HashMapMarketDepth, L2MarketDepth};
        use hftbacktest::types::Order;

        let mut depth = HashMapMarketDepth::new(1.0, 1.0);
        depth.update_bid_depth(100.0, 5.0, 0);
        let qm = RiskAdverseQueueModel::new();
        let mut order = Order::new(
            7,
            100,
            1.0,
            1.0,
            HbtSide::Buy,
            OrdType::Limit,
            TimeInForce::GTX,
        );
        qm.new_order(&mut order, &depth);
        // Чужая цена: глубина сменилась — очередь лишь подрезается минимумом,
        // фронт тот же.
        qm.depth(&mut order, 9.0, 7.0, &depth);
        assert_eq!(qm.is_filled(&mut order, &depth), 0.0);
        // Своя цена, мало: фронт 5 − 3 = 2 — исполнения нет.
        qm.trade(&mut order, 3.0, &depth);
        assert_eq!(qm.is_filled(&mut order, &depth), 0.0);
        // Своя цена, добивка: фронт 2 − 3 = −1 — исполнен весь лот.
        qm.trade(&mut order, 3.0, &depth);
        assert_eq!(qm.is_filled(&mut order, &depth), 1.0);
    }

    // -----------------------------------------------------------------------
    // Сквозной прогон драйвера на синтетике крейта: фид собирается руками,
    // неделя не нужна. Тип очереди в билдере — RiskAdverseQueueModel, тип
    // задержки — из latency_from_rtt.
    // -----------------------------------------------------------------------

    use hftbacktest::backtest::assettype::LinearAsset;
    use hftbacktest::backtest::data::Data;
    use hftbacktest::backtest::models::ConstantLatency;
    use hftbacktest::backtest::models::{CommonFees, RiskAdverseQueueModel, TradingValueFeeModel};
    use hftbacktest::backtest::{Backtest, ExchangeKind};
    use hftbacktest::backtest::{DataSource, L2AssetBuilder};
    use hftbacktest::depth::HashMapMarketDepth;
    use hftbacktest::prelude::Bot;
    use hftbacktest::types::{
        Event, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT, EXCH_EVENT,
        EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BUY_TRADE_EVENT,
        LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
    };

    /// Флаг глубины, видимый обеим сторонам бэктеста: локальной (по local_ts)
    /// и биржевой (по exch_ts). Экспорт пишет только LOCAL-флаги, здесь оба —
    /// иначе одна из сторон фид не увидит.
    fn depth_ev(bid: bool) -> u64 {
        if bid {
            LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
        } else {
            LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
        }
    }

    fn trade_ev(sell: bool) -> u64 {
        if sell {
            LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
        } else {
            LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
        }
    }

    fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> Event {
        Event {
            ev: depth_ev(bid),
            exch_ts,
            local_ts: exch_ts + 500,
            px,
            qty,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> Event {
        Event {
            ev: trade_ev(sell) | EXCH_EVENT | LOCAL_EVENT,
            exch_ts,
            local_ts: exch_ts + 500,
            px,
            qty,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    /// Бэктестер с очередью Decision 20 и задержкой из измеренной RTT.
    fn risk_adverse_backtest(feed: &[Event], exec_rtt_ns: i64) -> Backtest<HashMapMarketDepth> {
        let (entry, response) = latency_from_rtt(exec_rtt_ns);
        Backtest::builder()
            .add_asset(
                L2AssetBuilder::default()
                    .data(vec![DataSource::Data(Data::from_data(feed))])
                    .latency_model(ConstantLatency::new(entry, response))
                    .asset_type(LinearAsset::new(1.0))
                    .fee_model(TradingValueFeeModel::new(CommonFees::new(0.0002, 0.00055)))
                    .queue_model(RiskAdverseQueueModel::new())
                    .exchange(ExchangeKind::NoPartialFillExchange)
                    .depth(|| HashMapMarketDepth::new(1.0, 1.0))
                    .build()
                    .unwrap(),
            )
            .build()
            .unwrap()
    }

    fn drive_cfg() -> DriveConfig {
        DriveConfig {
            order_qty: 1.0,
            first_order_id: 1,
        }
    }

    /// Секунда в наносекундах для читаемости сценариев.
    const S: i64 = 1_000_000_000;

    /// Лонг по σ=+1: вход мейкером на бид 100, сделки съедают очередь за 2 с,
    /// книга уходит вверх с живым спредом, выход тейкером на 11-й секунде
    /// по 101. Итог: fills=1, обе колонки пропусков на месте, позиция плоская.
    #[test]
    fn driver_closes_a_maker_round_trip_on_synthetic_feed() {
        let feed = [
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            trade_at(S + S / 2, true, 100.0, 3.0),
            trade_at(S + 4 * S / 5, true, 100.0, 3.0),
            // Книга обязана держать спред: залоченная (бид ≥ аск) глубина
            // крейта прячет пересечённую сторону, и выхода не будет.
            depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
            depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
            depth_at(15 * S, false, 103.0, 5.0),
        ];
        let mut hbt = risk_adverse_backtest(&feed, 1_000_000);
        let rep = drive_cell(
            &mut hbt,
            0,
            VerdictCell::C2,
            &[Signal {
                t0_ns: S,
                sigma: SIGMA_LONG,
            }],
            &drive_cfg(),
        )
        .unwrap();
        assert!(!rep.incomplete, "фид длиннее выхода с запасом");
        assert_eq!(rep.signals, 1);
        assert_eq!(rep.fills.len(), 1, "вход исполнился за 2 с");
        assert_eq!(rep.misses, MissLedger::default());
        let fill = rep.fills[0];
        assert_eq!(fill.dir, 1);
        assert!(close(fill.entry_px, 100.0));
        assert!(close(fill.exit_px, 101.0));
        assert!(close(rep.mean_net_bps().unwrap(), 92.5));
        assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
    }

    /// Без сделок очередь не двигается: вход снимается через 2 с и считается
    /// пропуском именно по таймауту, а не по занятости.
    #[test]
    fn driver_counts_an_unfilled_entry_as_timeout_miss() {
        let feed = [
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            depth_at(15 * S, false, 102.0, 5.0),
        ];
        let mut hbt = risk_adverse_backtest(&feed, 1_000_000);
        let rep = drive_cell(
            &mut hbt,
            0,
            VerdictCell::C1,
            &[Signal {
                t0_ns: S,
                sigma: SIGMA_LONG,
            }],
            &drive_cfg(),
        )
        .unwrap();
        assert!(!rep.incomplete);
        assert_eq!(rep.fills.len(), 0);
        assert_eq!(rep.misses.timeout, 1);
        assert_eq!(rep.misses.busy, 0);
        assert_eq!(rep.mean_net_bps(), None);
        assert_eq!(hbt.position(0), 0.0);
    }

    /// Сигнал при открытой позиции — пропуск по занятости, а не второй вход:
    /// одна позиция за раз, в очередь не ставится.
    #[test]
    fn driver_counts_a_signal_inside_position_as_busy_miss() {
        let feed = [
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            trade_at(S + S / 2, true, 100.0, 3.0),
            trade_at(S + 4 * S / 5, true, 100.0, 3.0),
            depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
            depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
            depth_at(15 * S, false, 103.0, 5.0),
        ];
        let mut hbt = risk_adverse_backtest(&feed, 1_000_000);
        let rep = drive_cell(
            &mut hbt,
            0,
            VerdictCell::C2,
            &[
                Signal {
                    t0_ns: S,
                    sigma: SIGMA_LONG,
                },
                Signal {
                    t0_ns: 5 * S,
                    sigma: SIGMA_LONG,
                },
            ],
            &drive_cfg(),
        )
        .unwrap();
        assert!(!rep.incomplete);
        assert_eq!(rep.fills.len(), 1, "второй вход не ставился");
        assert_eq!(rep.misses.timeout, 0);
        assert_eq!(rep.misses.busy, 1);
        assert_eq!(hbt.position(0), 0.0);
    }

    /// Граница ядра как у издержек и экспорта: чистому ядру стратегии нечего
    /// делать в транспорте, стеночных часах и площадке. Имена заметаются
    /// склейкой, чтобы сам тест запрет не триггерил.
    #[test]
    fn module_stays_detached_from_transport_and_clocks() {
        const SRC: &str = include_str!("backtest.rs");
        let banned = [
            concat!("tok", "io"),
            concat!("Inst", "ant"),
            concat!("System", "Time"),
            concat!("std::", "time"),
            concat!("byb", "it::"),
            concat!("reqw", "est"),
            concat!("tungst", "enite"),
            concat!("elapse", "_bt"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
