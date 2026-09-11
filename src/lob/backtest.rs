//! `hftbacktest` со стратегией Decision 20, RTT из 6.4, очередь
//! `RiskAdverseQueueModel` (план, §6.3).
//!
//! Стратегия предрегистрирована целиком (Decision 20): вход мейкером у своей
//! стороны спреда с временем жизни ордера 2 с, выход тейкером ровно на
//! горизонте без стопа и без цели, одна позиция за раз, модель очереди —
//! `RiskAdverseQueueModel`. Отчёт строится на **произвольное число
//! профилей** (таск 11, история 32–34 спеки): полей `c1`/`c2`/`verdict_cell`
//! отменённого дизайна здесь больше нет — вызывающий (`commands::lob::
//! backtest`) приносит уже сгруппированные по `profile_id` сигналы, а этот
//! модуль их только гоняет и агрегирует.
//!
//! # Одна стратегия на бэктест и живую торговлю (A6)
//!
//! `drive_profile` не реализует вход/выход второй раз — это была бы
//! Reinvention (`interfaces.md`, правило 5). Экономику решения (вход
//! мейкером у своей стороны спреда, время жизни входа, горизонт выхода)
//! делает **`crate::lob::strategy::on_event<MD, B: Bot<MD>>`** (таск 15) —
//! ровно тот же символ функции, что пойдёт в `LiveBot` фазы 2 без правок.
//! Этот файл только продвигает часы стороны и решает, когда вооружить новый
//! круг и как классифицировать его исход (заполнение / таймаут / занятость)
//! по данным, которые `Bot<MD>` и так публикует (`orders`, `position`).
//!
//! # RTT из 6.4
//!
//! Замер 6.4 даёт две задержки — медиану и p95 (D-RTT). Обе прогоняются
//! отдельно (`build_backtest` дважды, с разной `exec_rtt_ns`), и обе кривые
//! PnL печатаются (гейт G4-в, `PLAN.md`). Вся измеренная RTT кладётся на
//! входное плечо (`response = 0`) — консервативное допущение, см.
//! `latency_from_rtt`.
//!
//! # Done-condition 6.3 буквально
//!
//! Отчёт на профиль несёт: PnL-кривую на обеих RTT, число заполнений, число
//! пропущенных сигналов отдельно по причине (`MissLedger`), агрегаты
//! `fill`/`net_fill` через `costs::{fill_rate, net_fill_bps,
//! net_fill_interval}` (alpha — из спеки, не изобретать), и сравнение с
//! оценкой из таблицы профилей (таск 10) — приносит вызывающий, этот модуль
//! `shortlist`/файловый ввод не трогает (граница модулей: чистое ядро не
//! тянет CLI и площадку — см. грепом-тест в конце файла, тот же приём, что
//! `levels.rs`/`strategy.rs`).
//!
//! Модуль не хранит состояния и не выделяет память вне отчёта и очереди
//! самого крейта.

use hftbacktest::backtest::assettype::LinearAsset;
use hftbacktest::backtest::data::Data;
use hftbacktest::backtest::models::{
    CommonFees, ConstantLatency, RiskAdverseQueueModel, TradingValueFeeModel,
};
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::depth::{HashMapMarketDepth, MarketDepth};
use hftbacktest::types::{Bot, ElapseResult, Event, Side as HbtSide, Status};

use crate::lob::costs::{
    fill_rate, format_fill_column, net_fill_bps, net_fill_interval, FillObservation,
    NetFillInterval, MAKER_FEE_BPS, ROUNDTRIP_FEES_BPS, TAKER_FEE_BPS,
};
use crate::lob::strategy::{on_event, Action, StrategyState};

// ---------------------------------------------------------------------------
// Константы Decision 20. Каждое число — из плана.
// ---------------------------------------------------------------------------

/// Время жизни входного мейкер-ордера: 2 с в наносекундах. Не исполнился —
/// снимается, сигнал считается пропущенным (`MissReason::EntryTimeout`).
pub const ENTRY_TTL_NS: i64 = 2_000_000_000;

/// Горизонт позиции: выход тейкером ровно на `t₀ + 10 с`, без стопа и цели.
/// Тот же горизонт, что у вердиктного гейта G4: меряется markout, выход
/// совпадает.
pub const HOLD_NS: i64 = 10_000_000_000;

/// σ уровня на стороне бида (Decision 14): подразумевает шорт.
pub const SIGMA_SHORT: i8 = -1;

/// σ уровня на стороне аска (Decision 14): подразумевает лонг.
pub const SIGMA_LONG: i8 = 1;

// ---------------------------------------------------------------------------
// Вход: сигналы триггера уровня, уже сгруппированные по профилю.
// ---------------------------------------------------------------------------

/// Один сигнал стратегии: срабатывание условия уровня в момент `t₀`.
/// Принадлежность к профилю решает вызывающий (группировка по `profile_id` —
/// `commands::lob::backtest`); сюда сигнал приходит уже отобранным, вместе
/// со стороной σ по Decision 14.
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
    curve
        .last()
        .map(|last| last / crate::stats::count_f64(fills.len()))
}

// ---------------------------------------------------------------------------
// Сравнение с таблицей профилей (таск 10) и гейт G4.
// ---------------------------------------------------------------------------

/// Оценка профиля из `docs/findings/profiles-<дата>.csv` (таск 10). Это
/// чистое ядро CSV не читает — значения приносит вызывающий
/// (`commands::lob::backtest::read_table`), иначе `lob/backtest` тянул бы
/// файловый ввод в свою границу модуля.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TableEstimate {
    /// `net_bps` таблицы (без взвешивания на `fill`).
    pub net_bps: Option<f64>,
    /// `net_fill` таблицы — база сравнения, когда она измерялась.
    pub net_fill_bps: Option<f64>,
    /// `true`, когда источник явно не мерил `net_fill`/`fill`
    /// (`fill_model=none` — колонка несёт литерал `not_measured`, не
    /// число и не отсутствие данных). Сравнение в этом случае падает на
    /// `net_bps`, а колонка `table_net_fill_bps` печатает `not_measured`
    /// буквально — не путать «не измерялось» с «измерено и пусто».
    pub net_fill_not_measured: bool,
}

/// Сравнение реализованного `net_fill` бэктеста с оценкой таблицы профилей.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TableComparison {
    /// `net_bps` таблицы, для контекста (`None` — строки профиля нет).
    pub table_net_bps: Option<f64>,
    /// `net_fill` таблицы, если он измерялся (`None`, когда `not_measured`
    /// или строки нет).
    pub table_net_fill_bps: Option<f64>,
    /// `true` — таблица явно не мерила `net_fill` (печатать `not_measured`,
    /// не `none`).
    pub table_net_fill_not_measured: bool,
    /// Реализованный `net_fill` бэктеста (медианная RTT).
    pub realized_net_fill_bps: Option<f64>,
    /// Разность `реализация − база` в bps: база — `net_fill` таблицы, а
    /// если он не измерялся — `net_bps` (координатор: «тогда сравнение по
    /// `net_bps`»). `None` — нет любого из входов.
    pub diff_net_fill_bps: Option<f64>,
}

/// Разность реализации и таблицы профилей. Отказ любого входа — `None`
/// разности, а не ноль: совпадение с оценкой надо показать, а не
/// предположить.
pub fn compare_with_table(
    table: Option<TableEstimate>,
    realized_net_fill_bps: Option<f64>,
) -> TableComparison {
    let (table_net_bps, table_net_fill_bps, table_net_fill_not_measured) = match table {
        Some(t) => (t.net_bps, t.net_fill_bps, t.net_fill_not_measured),
        None => (None, None, false),
    };
    let baseline = if table_net_fill_not_measured {
        table_net_bps
    } else {
        table_net_fill_bps
    };
    let diff_net_fill_bps = match (baseline, realized_net_fill_bps) {
        (Some(a), Some(b)) if a.is_finite() && b.is_finite() => Some(b - a),
        _ => None,
    };
    TableComparison {
        table_net_bps,
        table_net_fill_bps,
        table_net_fill_not_measured,
        realized_net_fill_bps,
        diff_net_fill_bps,
    }
}

impl TableComparison {
    /// Строка сравнения для отчёта: оба числа и разность; `table_net_fill`
    /// печатает `not_measured` буквально, когда таблица его не мерила.
    pub fn format_line(&self) -> String {
        let table_net_fill = if self.table_net_fill_not_measured {
            "not_measured".to_string()
        } else {
            fmt_opt(self.table_net_fill_bps)
        };
        format!(
            "vs_table: table_net_fill={table_net_fill} realized_net_fill={} diff={}",
            fmt_opt(self.realized_net_fill_bps),
            fmt_opt(self.diff_net_fill_bps)
        )
    }
}

/// Вердикт гейта G4. Чистый PnL положителен при медианном RTT и остаётся
/// положительным при 95-м перцентиле — иначе красный (G4-в, `PLAN.md`).
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
// Сторона и цены по σ (Decision 14 + Decision 19). Переиспользуются
// `lob::strategy` как есть (не Reinvention: `interfaces.md`, «Из таска 15»).
// ---------------------------------------------------------------------------

/// Сторона входа по σ: бид (`-1`) подразумевает шорт, аск (`+1`) — лонг
/// (Decision 14 делает предсказанный знак положительным во всех уровнях).
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
// Сутки как целый кластер для `costs::net_fill_interval` (тот же смысл
// кластера, что везде в `stats`/`cells`).
// ---------------------------------------------------------------------------

/// Наносекунд в сутках — физическая константа, не параметр модели.
const NANOS_PER_DAY: i64 = 86_400_000_000_000;

/// Индекс суток UTC по `t0_ns`. Целочисленное деление — сутки как целый
/// кластер (A1), без календарной библиотеки: для группировки наблюдений
/// достаточно детерминированной границы, а не человекочитаемой даты.
fn day_index_ns(t0_ns: i64) -> i64 {
    t0_ns.div_euclid(NANOS_PER_DAY)
}

// ---------------------------------------------------------------------------
// Прогон одного профиля поверх `Bot<MD>`, мотором — `strategy::on_event`.
// ---------------------------------------------------------------------------

/// Настройки прогона профиля.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DriveConfig {
    /// Размер круга. План: минимальный лот биржи (Decision 22).
    pub order_qty: f64,
    /// Первый идентификатор ордеров; дальше — по порядку, каждый ордер
    /// уникален (требование трейта).
    pub first_order_id: u64,
}

/// Итог прогона одного профиля на одной RTT-сценарии: done-condition 6.3
/// буквально — заполнения, пропуски раздельно по причине, наблюдения для
/// `costs::{fill_rate, net_fill_bps, net_fill_interval}`.
#[derive(Debug, Clone, PartialEq)]
pub struct ProfileRun {
    /// Сигналов подано в драйвер.
    pub signals: u64,
    /// Закрытые круги в порядке исполнения.
    pub fills: Vec<Fill>,
    /// Пропуски раздельно по причине (обязательная колонка).
    pub misses: MissLedger,
    /// Одно наблюдение на попытку (заполнена или нет) — вход `costs`.
    pub observations: Vec<FillObservation>,
    /// Данные кончились раньше, чем позиция закрылась или время вышло:
    /// итог неполон, вердикт по нему не выносится.
    pub incomplete: bool,
}

impl ProfileRun {
    /// Число заполнений (закрытых кругов).
    pub fn n_fills(&self) -> usize {
        self.fills.len()
    }

    /// Средний чистый результат круга в bps (не взвешен на `fill`).
    pub fn mean_net_bps(&self) -> Option<f64> {
        mean_net_bps(&self.fills)
    }

    /// Доля исполнившихся входов (R06) — `costs::fill_rate`.
    pub fn fill_rate(&self) -> Option<f64> {
        fill_rate(&self.observations)
    }

    /// `net_fill` по наблюдению (R07) — `costs::net_fill_bps`.
    pub fn net_fill_bps(&self) -> Option<f64> {
        net_fill_bps(&self.observations)
    }

    /// Совместный интервал `net_fill` — `costs::net_fill_interval` с
    /// каноническими `alpha`/`replications` гейта (`stats::GATE_ALPHA`,
    /// `stats::BOOTSTRAP_REPLICATIONS`), тот же выбор, что уже использует
    /// `commands::lob::pilot` для родственного интервала.
    pub fn net_fill_interval(&self) -> Option<NetFillInterval> {
        net_fill_interval(
            &self.observations,
            crate::stats::GATE_ALPHA,
            crate::stats::BOOTSTRAP_REPLICATIONS,
            0,
        )
    }

    /// Строки профиля для отчёта: заполнения, обе колонки пропусков, кривая
    /// компактно (длина, финал, минимум), `fill`/`net_fill`/нижняя граница.
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
            Some(curve) => match curve.last() {
                Some(&last) => {
                    let min = curve.iter().fold(f64::INFINITY, |a, b| a.min(*b));
                    lines.push(format!(
                        "{name} pnl_bps: n={} final={:.4} min={:.4}",
                        curve.len(),
                        last,
                        min
                    ));
                }
                None => lines.push(format!("{name} pnl_bps: none (no fills)")),
            },
            None => lines.push(format!("{name} pnl_bps: none (no fills)")),
        }
        lines.push(format!(
            "{name} mean_net_bps={}",
            fmt_opt(self.mean_net_bps())
        ));
        lines.push(format!(
            "{name} fill={} net_fill={} net_fill_lower={}",
            format_fill_column(self.fill_rate()),
            fmt_opt(self.net_fill_bps()),
            fmt_opt(self.net_fill_interval().map(|iv| iv.lower_bps)),
        ));
        lines
    }
}

/// Шаг опроса `on_event` при активном круге. Не модельная величина и не
/// параметр стратегии (тот же класс, что был у снятого `EXIT_RESP_TIMEOUT_NS`
/// раньше) — только гранулярность, с которой драйвер замечает переходы фазы
/// внутри `on_event` (`interfaces.md`: модуль `lob/strategy` «прячет:
/// триггер, состояние», наружу виден только `Action`). Даёт джиттер ≤ шага
/// на моментах входа/выхода — 10 мс против `ENTRY_TTL_NS` = 2 с и `HOLD_NS` =
/// 10 с, то есть ≤ 0.5% и ≤ 0.1% соответственно; исполнение цены решает
/// очередь крейта на момент фактического пересечения, шаг её не трогает.
const ON_EVENT_POLL_STEP_NS: i64 = 10_000_000;

/// Прогон стратегии `on_event` (таск 15) по сигналам одного профиля поверх
/// любого `Bot<MD>` — бэктестера или живой стороны (A6: тот же код без
/// правок). Экономику решения не дублирует: `on_event` сама решает, когда
/// войти, когда снять неисполнившийся вход и когда выйти. Этот драйвер
/// только продвигает часы стороны и классифицирует исход каждого
/// вооружённого круга по тому, что `Bot<MD>` и так публикует.
///
/// Одна позиция за раз: сигнал, чей `t0` приходится на время удержания
/// предыдущего (успешно исполнившегося) круга, — пропуск по занятости; окно
/// занятости берётся из **фактического** времени исполнения выхода
/// (`Order::exch_timestamp`), а не из номинального `t0 + HOLD_NS`.
pub fn drive_profile<B, MD>(
    bot: &mut B,
    asset_no: usize,
    signals: &[Signal],
    cfg: &DriveConfig,
) -> Result<ProfileRun, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut order: Vec<Signal> = signals.to_vec();
    order.sort_by_key(|s| s.t0_ns);
    let mut fills: Vec<Fill> = Vec::new();
    let mut misses = MissLedger::default();
    let mut observations: Vec<FillObservation> = Vec::new();
    let mut next_id = cfg.first_order_id;
    let mut incomplete = false;
    let mut blocked_until_ns: i64 = i64::MIN;

    let miss_observation = |t0_ns: i64| FillObservation {
        day_cluster: day_index_ns(t0_ns),
        net_bps: 0.0,
        filled: false,
    };

    // Инициализация часов стороны: свежий бэктестер стоит на максимуме до
    // первого продвижения, живая сторона — на стенке; нулевой шаг первую
    // ставит на первый фид, второй безвреден.
    if bot.elapse(0)? == ElapseResult::EndOfData {
        return Ok(ProfileRun {
            signals: order.len() as u64,
            fills,
            misses,
            observations,
            incomplete: true,
        });
    }

    'signals: for sig in &order {
        // Неизвестная σ — не сигнал стратегии: пропускается без счёта, как
        // чужой символ в счётчике `watch` (молчание здесь — фильтр, а не
        // потеря: выборка профиля его не содержит по построению).
        if entry_side(sig.sigma).is_none() {
            continue;
        }
        if sig.t0_ns < blocked_until_ns {
            misses.record(MissReason::PositionBusy);
            observations.push(miss_observation(sig.t0_ns));
            continue;
        }
        // Догнать время сигнала часами стороны.
        let now = bot.current_timestamp();
        if sig.t0_ns > now && bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
            incomplete = true;
            break;
        }
        // Двойная проверка занятости (как у `blocked_until_ns` выше): по
        // факту тоже, на случай если позиция открыта извне драйвера.
        if bot.position(asset_no) != 0.0 {
            misses.record(MissReason::PositionBusy);
            observations.push(miss_observation(sig.t0_ns));
            continue;
        }

        let mut state = StrategyState::new(asset_no, sig.sigma, cfg.order_qty, next_id);
        // Один круг тратит не больше двух ордеров (вход, выход); запас —
        // страховка от коллизии id со следующим кругом, не экономическая
        // величина.
        next_id = next_id.saturating_add(4);

        let (entry_id, side) = match on_event(bot, &mut state)? {
            Action::EntrySubmitted { order_id, side, .. } => (order_id, side),
            Action::Idle => {
                // Книга не была готова в момент сигнала — попытки не было и
                // не будет: тот же исход, что «не исполнился за 2 с».
                misses.record(MissReason::EntryTimeout);
                observations.push(miss_observation(sig.t0_ns));
                continue;
            }
            other => unreachable!("свежее состояние не могло вернуть {other:?}"),
        };

        let mut timed_out = false;
        let mut exit_id: Option<u64> = None;
        loop {
            if bot.elapse(ON_EVENT_POLL_STEP_NS)? == ElapseResult::EndOfData {
                incomplete = true;
                break 'signals;
            }
            match on_event(bot, &mut state)? {
                Action::EntryTimedOut { .. } => timed_out = true,
                Action::ExitSubmitted { order_id, .. } => exit_id = Some(order_id),
                Action::Idle | Action::EntrySubmitted { .. } => {}
            }
            if state.is_idle() {
                break;
            }
        }

        if timed_out {
            misses.record(MissReason::EntryTimeout);
            observations.push(miss_observation(sig.t0_ns));
        } else if let Some(exit_id) = exit_id {
            let entry_px = bot
                .orders(asset_no)
                .get(&entry_id)
                .filter(|o| o.status == Status::Filled)
                .map(hftbacktest::types::Order::exec_price);
            let exit_info = bot
                .orders(asset_no)
                .get(&exit_id)
                .filter(|o| o.status == Status::Filled)
                .map(|o| (o.exec_price(), o.exch_timestamp));
            match (entry_px, exit_info) {
                (Some(entry_px), Some((exit_px, exit_ts))) => {
                    let dir = if side == HbtSide::Buy { 1 } else { -1 };
                    let fill = Fill {
                        dir,
                        entry_px,
                        exit_px,
                        qty: cfg.order_qty,
                    };
                    let net = roundtrip_net_bps(&fill);
                    observations.push(FillObservation {
                        day_cluster: day_index_ns(sig.t0_ns),
                        net_bps: net.unwrap_or(0.0),
                        filled: net.is_some(),
                    });
                    fills.push(fill);
                    blocked_until_ns = exit_ts;
                }
                _ => {
                    // Круг «завершился» (is_idle), но заполнения найти
                    // нельзя — рассинхрон с данными; отчёт честно
                    // помечается неполным, а не тихой нулевой строкой.
                    incomplete = true;
                }
            }
        } else {
            // is_idle без выхода и без таймаута не должно случаться при
            // корректном `on_event`; неполнота честнее тихого нуля.
            incomplete = true;
        }
        bot.clear_inactive_orders(Some(asset_no));
    }
    bot.clear_inactive_orders(Some(asset_no));

    Ok(ProfileRun {
        signals: order.len() as u64,
        fills,
        misses,
        observations,
        incomplete,
    })
}

// ---------------------------------------------------------------------------
// Отчёт на N профилей (история 32–34): медиана и p95 RTT, сравнение с
// таблицей профилей, вердикт G4.
// ---------------------------------------------------------------------------

/// Итог профиля: прогон на медианной и на p95 RTT, сравнение с таблицей
/// профилей (по медианной — основной сценарий), вердикт G4 по обеим.
#[derive(Debug, Clone, PartialEq)]
pub struct ProfileReport {
    /// Идентификатор профиля — тот же, что в `profiles-<дата>.csv`.
    pub profile_id: String,
    /// Прогон на медианной замеренной RTT.
    pub median: ProfileRun,
    /// Прогон на p95 замеренной RTT.
    pub p95: ProfileRun,
    /// Сравнение реализованного `net_fill` (медианная RTT) с таблицей.
    pub comparison: TableComparison,
    /// Вердикт гейта G4.
    pub g4: G4Verdict,
}

impl ProfileReport {
    /// Строки профиля: заголовок, обе RTT-сценарии, сравнение, вердикт.
    pub fn summary_lines(&self) -> Vec<String> {
        let mut lines = Vec::with_capacity(10);
        lines.push(format!("profile={}", self.profile_id));
        lines.extend(self.median.summary_lines("median_rtt"));
        lines.extend(self.p95.summary_lines("p95_rtt"));
        lines.push(self.comparison.format_line());
        lines.push(self.g4.to_string());
        lines
    }
}

/// Собрать отчёт профиля из двух готовых прогонов (медиана, p95) и
/// (опциональной) строки таблицы профилей.
pub fn build_profile_report(
    profile_id: String,
    median: ProfileRun,
    p95: ProfileRun,
    table: Option<TableEstimate>,
) -> ProfileReport {
    let comparison = compare_with_table(table, median.net_fill_bps());
    let g4 = decide_g4(median.mean_net_bps(), p95.mean_net_bps());
    ProfileReport {
        profile_id,
        median,
        p95,
        comparison,
        g4,
    }
}

/// Отчёт бэктеста на произвольное число профилей (история 32–34 спеки, done
/// после сноса `c1`/`c2`/`verdict_cell`).
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestReport {
    pub profiles: Vec<ProfileReport>,
}

impl BacktestReport {
    pub fn new(profiles: Vec<ProfileReport>) -> Self {
        Self { profiles }
    }

    /// Строки всех профилей подряд — то, что печатает `commands::lob::backtest`
    /// и что таск 13 читает в шапку шорт-листа.
    pub fn summary_lines(&self) -> Vec<String> {
        self.profiles
            .iter()
            .flat_map(ProfileReport::summary_lines)
            .collect()
    }
}

// ---------------------------------------------------------------------------
// Строитель `Backtest` крейта: движок Decision 20 целиком в одном месте.
// ---------------------------------------------------------------------------

/// Строит `Backtest` крейта с движком Decision 20: `RiskAdverseQueueModel`,
/// комиссии `costs::{MAKER_FEE_BPS, TAKER_FEE_BPS}`, задержка из замеренной
/// RTT (`latency_from_rtt`). `events` — уже переведённый в формат крейта
/// поток; перевод из `Feed`/площадки — забота вызывающего
/// (`commands::lob::backtest`), не этого файла (грепом-тест внизу).
pub fn build_backtest(
    events: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_rtt_ns: i64,
) -> Backtest<HashMapMarketDepth> {
    let (entry, response) = latency_from_rtt(exec_rtt_ns);
    Backtest::builder()
        .add_asset(
            L2AssetBuilder::default()
                .data(vec![DataSource::Data(Data::from_data(events))])
                .latency_model(ConstantLatency::new(entry, response))
                .asset_type(LinearAsset::new(1.0))
                .fee_model(TradingValueFeeModel::new(CommonFees::new(
                    MAKER_FEE_BPS / 10_000.0,
                    TAKER_FEE_BPS / 10_000.0,
                )))
                .queue_model(RiskAdverseQueueModel::new())
                .exchange(ExchangeKind::NoPartialFillExchange)
                .depth(move || HashMapMarketDepth::new(tick_size, lot_size))
                .build()
                .unwrap(),
        )
        .build()
        .unwrap()
}

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

    /// Сравнение с таблицей профилей: разность реализации и таблицы,
    /// отказы — `None`.
    #[test]
    fn table_comparison_is_realized_minus_table_estimate() {
        let table = Some(TableEstimate {
            net_bps: Some(5.0),
            net_fill_bps: Some(7.0),
            net_fill_not_measured: false,
        });
        let c = compare_with_table(table, Some(92.5));
        assert!(close(c.diff_net_fill_bps.unwrap(), 85.5));
        assert_eq!(compare_with_table(None, Some(1.0)).diff_net_fill_bps, None);
        assert_eq!(
            compare_with_table(
                Some(TableEstimate {
                    net_bps: Some(1.0),
                    net_fill_bps: Some(1.0),
                    net_fill_not_measured: false,
                }),
                None
            )
            .diff_net_fill_bps,
            None
        );
        let line = c.format_line();
        assert!(
            line.contains("vs_table"),
            "строка обязана называться: {line}"
        );
        assert!(line.contains("diff=85.5000"), "разность на месте: {line}");
    }

    /// Координатор: `net_fill` таблицы `not_measured` — сравнение падает на
    /// `net_bps`, а колонка печатает `not_measured` буквально, не `none`.
    #[test]
    fn not_measured_net_fill_falls_back_to_net_bps_for_the_diff() {
        let table = Some(TableEstimate {
            net_bps: Some(10.0),
            net_fill_bps: None,
            net_fill_not_measured: true,
        });
        let c = compare_with_table(table, Some(92.5));
        assert!(close(c.diff_net_fill_bps.unwrap(), 82.5), "{c:?}");
        assert_eq!(c.table_net_fill_bps, None);
        let line = c.format_line();
        assert!(
            line.contains("table_net_fill=not_measured"),
            "not_measured обязан печататься буквально: {line}"
        );
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

    /// Done-condition читается в строках отчёта профиля: заполнения, обе
    /// колонки пропусков, `fill`/`net_fill`, сравнение с таблицей, G4.
    #[test]
    fn profile_report_carries_every_done_item() {
        let median = ProfileRun {
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
            observations: vec![
                FillObservation {
                    day_cluster: 0,
                    net_bps: 92.5,
                    filled: true,
                },
                FillObservation {
                    day_cluster: 0,
                    net_bps: 0.0,
                    filled: false,
                },
                FillObservation {
                    day_cluster: 0,
                    net_bps: 0.0,
                    filled: false,
                },
            ],
            incomplete: false,
        };
        let p95 = ProfileRun {
            signals: 3,
            fills: vec![],
            misses: MissLedger {
                timeout: 3,
                busy: 0,
            },
            observations: vec![
                FillObservation {
                    day_cluster: 0,
                    net_bps: 0.0,
                    filled: false,
                };
                3
            ],
            incomplete: false,
        };
        let table = Some(TableEstimate {
            net_bps: Some(5.0),
            net_fill_bps: Some(10.0),
            net_fill_not_measured: false,
        });
        let report = build_profile_report("cross:SOL|eaten|near".to_string(), median, p95, table);
        let text = report.summary_lines().join("\n");
        assert!(text.contains("profile=cross:SOL|eaten|near"), "{text}");
        assert!(text.contains("fills=1"), "{text}");
        assert!(text.contains("missed_timeout=1"), "{text}");
        assert!(text.contains("missed_busy=1"), "{text}");
        assert!(text.contains("vs_table"), "{text}");
        assert!(text.contains("pnl_bps"), "{text}");
        assert!(text.contains("fill="), "{text}");
        assert!(text.contains("net_fill="), "{text}");
        assert!(
            !report.g4.is_pass(),
            "p95 без заполнений — не может быть Pass: {text}"
        );
        assert!(text.contains("G4 RED"), "{text}");
    }

    // -----------------------------------------------------------------------
    // Очередь RiskAdverseQueueModel на фикстуре крейта: позиция двигается
    // только сделками на той же цене (самое консервативное допущение).
    // -----------------------------------------------------------------------

    /// Очередь встаёт за видимым объёмом, сделка на чужой цене её не двигает,
    /// сделка на своей — двигает, исполнение наступает при съеденной очереди.
    #[test]
    fn risk_adverse_queue_moves_only_on_same_price_trades() {
        use hftbacktest::backtest::models::QueueModel;
        use hftbacktest::depth::L2MarketDepth;
        use hftbacktest::types::{OrdType, TimeInForce};

        let mut depth = HashMapMarketDepth::new(1.0, 1.0);
        depth.update_bid_depth(100.0, 5.0, 0);
        let qm = RiskAdverseQueueModel::new();
        let mut order = hftbacktest::types::Order::new(
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
    // неделя не нужна. Мотор — `strategy::on_event`, не копия экономики.
    // -----------------------------------------------------------------------

    use hftbacktest::types::{
        EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT, EXCH_EVENT,
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

    fn drive_cfg() -> DriveConfig {
        DriveConfig {
            order_qty: 1.0,
            first_order_id: 1,
        }
    }

    /// Секунда в наносекундах для читаемости сценариев.
    const S: i64 = 1_000_000_000;

    /// Лонг по σ=+1: вход мейкером на бид 100, сделки съедают очередь за 2 с,
    /// книга уходит вверх с живым спредом, выход тейкером по 101. Итог:
    /// fills=1, обе колонки пропусков на месте, позиция плоская.
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
            depth_at(30 * S, false, 103.0, 5.0),
        ];
        let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
        let rep = drive_profile(
            &mut hbt,
            0,
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
        assert_eq!(rep.observations.len(), 1);
        assert!(rep.observations[0].filled);
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
        let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
        let rep = drive_profile(
            &mut hbt,
            0,
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
        assert_eq!(rep.observations.len(), 1);
        assert!(!rep.observations[0].filled);
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
            depth_at(30 * S, false, 103.0, 5.0),
        ];
        let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
        let rep = drive_profile(
            &mut hbt,
            0,
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
        assert_eq!(rep.observations.len(), 2);
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
            concat!("crate::", "feed"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
