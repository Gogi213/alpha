//! `hftbacktest` со стратегией Decision 20, RTT из 6.4, очередь
//! `RiskAdverseQueueModel` (план, §6.3). С F3 плана 2026-09-20 модель очереди
//! и исполнения — параметр (`QueueModelKind`): прежняя пара
//! `RiskAdverseQueueModel` + `NoPartialFillExchange` или крейтовые
//! `ProbQueueModel<PowerProbQueueFunc(n)>` + `PartialFillExchange`; три пути
//! исполнения крейта — в doc-комментарии `build_backtest`.
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
use hftbacktest::backtest::data::{Data, DataPtr};
use hftbacktest::backtest::models::{
    CommonFees, LatencyModel, PowerProbQueueFunc, ProbQueueModel, RiskAdverseQueueModel,
    TradingValueFeeModel,
};
use hftbacktest::backtest::BacktestError;
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::depth::{L2MarketDepth, MarketDepth};
use hftbacktest::types::{
    Bot, ElapseResult, Event, OrdType, Order, Side as HbtSide, Status, TimeInForce, BUY_EVENT,
    EXCH_BID_DEPTH_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, SELL_EVENT,
};

use crate::lob::costs::{
    fill_rate, format_fill_column, leg_fee_bps, net_fill_bps, net_fill_interval, FillObservation,
    NetFillInterval, MAKER_FEE_BPS, TAKER_FEE_BPS,
};
use crate::lob::strategy::{
    on_event, Action, EntryCancelReason, ExitReason, OrphanCarry, StrategyState, TradePlan,
    MAX_ENTRY_LEGS,
};

mod compact;
pub mod fast_depth;
#[allow(dead_code)]
pub mod fast_hold;
mod levels;
#[allow(dead_code)]
pub mod sched;
#[allow(dead_code)]
pub mod shared_depth;
pub mod shared_driver;
pub mod shared_engine;
pub mod shared_multi;
use fast_depth::FastMarketDepth;
mod trim;
mod window_depth;
pub use compact::{CompactEvent, EventKind, EventRows};
pub use trim::{kept_rows, TrimRows};
pub(crate) use window_depth::round_half_away;
pub use window_depth::WindowDepth;

/// Шаг номеров заявок между сигналами: круг тратит до `MAX_ENTRY_LEGS` ног входа,
/// заявку выхода, тейкерское добивание и гашение сироты (F8c) — прежний запас
/// «+4» с лестницей формы F6 давал коллизию id ещё открытой заявки с входом
/// следующего сигнала (ревью 22.09, п. 5). Не экономическая величина.
const ID_STRIDE: u64 = MAX_ENTRY_LEGS as u64 + 4;

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
///
/// Размер и цена входа — **по факту исполнения**, а не по плану (F3 плана
/// 2026-09-20): модель очереди по объёму исполняет заявку частично, и круг
/// на частичном входе — норма (В-78). У прежней модели (`RiskAdverse`,
/// полное исполнение) `qty` остаётся плановым размером, `entry_vwap` —
/// прежней `entry_px`, `fill_frac` — единицей: гейт «байт в байт» этого не
/// меняет.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fill {
    /// Направление: `+1` — лонг (покупка, затем продажа),
    /// `-1` — шорт (продажа, затем покупка).
    pub dir: i8,
    /// Цена входа (исполнение мейкера): средняя по исполненным ногам с
    /// **равными** весами — как было до F3 (у лестницы ноги равного размера,
    /// так что при полном исполнении это она же и есть).
    pub entry_px: f64,
    /// Цена выхода (исполнение тейкера).
    pub exit_px: f64,
    /// Реально исполненный размер входа: накопленный исполненный объём ног
    /// (`qty − leaves_qty`; у крейта `exec_qty` — объём последнего исполнения,
    /// а не сумма). При полном исполнении равен плановому размеру круга.
    pub qty: f64,
    /// Вход исполнился тейкером (лимит пересёк книгу; у лестницы — хотя бы
    /// одна нога) — по флагу `maker` ордера крейта (В-63).
    pub entry_taker: bool,
    /// Выход исполнился тейкером: стоп/дедлайн/досрочный/трейл — по рынку,
    /// тейк — лимитом (мейкер); тоже по флагу крейта.
    pub exit_taker: bool,
    /// Средневзвешенная по исполненному размеру цена входа
    /// (`Σ exec_px × исполненное / Σ исполненное`) — по ней считает
    /// `roundtrip_net_bps`. Цена ноги — цена последнего исполнения (крейт
    /// хранит только её; у ноги из одного исполнения — точно). При полном
    /// исполнении равна `entry_px` (и прежнему значению `entry_px` — гейт F3).
    pub entry_vwap: f64,
    /// Доля исполненного от заказанного: `qty / Σ order.qty` по **принятым**
    /// биржей ногам (отвергнутая пост-онли нога — отказ, а не заказ: её
    /// считает `legs_rejected`, F4). При полном исполнении равна `1.0`.
    pub fill_frac: f64,
    /// Сколько ног входа исполнилось (хоть частично).
    pub legs_filled: u8,
    /// Сколько ног входа биржа **не поставила** (`Expired` — пост-онли заявка
    /// пересекла спред, `Rejected` — обычная): по В-72 такая нога отказ, а не
    /// заказ, в `fill_frac` она не входит и считается отдельно. Колонка
    /// `legs_rejected` в `rounds.csv`; сумма по кругам — `n_rejected_postonly`
    /// в `forms.csv` (F4).
    pub legs_rejected: u8,
    /// Исполнение пришло **обновлением лучшей цены** (`on_best_*_update`), а
    /// не сделкой, которая могла бы исполнить эту ногу: в буфере последних
    /// сделок шага не было сделки по нашу сторону цены. Это путь (3) из
    /// doc-комментария `build_backtest` — оптимистичный по размеру
    /// (крейт исполняет весь остаток, а не объём лучшей цены).
    pub fill_by_cross: bool,
}

/// Чистый результат круга в bps: направленная доходность минус комиссии
/// **по ногам** (В-63: `costs::leg_fee_bps` — мейкер 1.26 / тейкер 3.15 bps
/// после возврата; тейк лимитом — мейкер+мейкер 2.52, стоп по рынку —
/// мейкер+тейкер 4.41). Цена входа — `entry_vwap` (средневзвешенная по
/// исполненным ногам, F3; при полном исполнении равна прежней `entry_px`,
/// так что прежние значения не меняются). `None` при неположительном входе
/// или неконечных ценах: отсутствие данных не есть нулевой результат (то же
/// правило, что неконечный markout в 5.2).
pub fn roundtrip_net_bps(fill: &Fill) -> Option<f64> {
    if !fill.entry_vwap.is_finite() || !fill.exit_px.is_finite() || fill.entry_vwap <= 0.0 {
        return None;
    }
    let dir = match fill.dir {
        1 => 1.0,
        -1 => -1.0,
        _ => return None,
    };
    let gross = dir * (fill.exit_px - fill.entry_vwap) / fill.entry_vwap * 10_000.0;
    Some(gross - leg_fee_bps(fill.entry_taker) - leg_fee_bps(fill.exit_taker))
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

/// Задержки исполнения по типу запроса — измерены `lob latency` с сервера
/// (`docs/findings/latency-2026-09-19.md`, В-68): постановка лимитки, снятие,
/// рыночный ордер, каждая — от отправки до подтверждения биржей, нс. Одно число
/// на все три (`uniform`) — прежняя форма В-37 («20 мс на всё»). Крейт получает
/// каждую через `latency_from_rtt`: целиком на входное плечо, ответное — ноль.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExecLatency {
    pub place_ns: i64,
    pub cancel_ns: i64,
    pub taker_ns: i64,
}

impl ExecLatency {
    pub const fn uniform(rtt_ns: i64) -> Self {
        Self {
            place_ns: rtt_ns,
            cancel_ns: rtt_ns,
            taker_ns: rtt_ns,
        }
    }

    pub fn is_uniform(&self) -> bool {
        self.place_ns == self.cancel_ns && self.cancel_ns == self.taker_ns
    }

    /// Подпись для шапок артефактов: одно число — назначено (В-37), три —
    /// измерено (В-68).
    pub fn provenance(&self) -> &'static str {
        if self.is_uniform() {
            "assumed(В-37)"
        } else {
            "measured(lob latency, В-68)"
        }
    }
}

/// Измеренные медианы WS trade с сервера коллектора (В-68, `lob latency`
/// 2026-09-19, `docs/findings/latency-2026-09-19.md`): постановка 4.20 мс,
/// снятие 3.98, рыночный 5.65. Флаги бэктестов эту константу **не** берут по
/// умолчанию (правило В-37: число называют явно) — она для справочных полей
/// (`lob dashboard` печатает, какую задержку ждёт бэктест).
pub const MEASURED_LATENCY: ExecLatency = ExecLatency {
    place_ns: 4_200_000,
    cancel_ns: 3_980_000,
    taker_ns: 5_650_000,
};

/// `--median-rtt-ns 20000000` (одно число на всё) или
/// `--median-rtt-ns place=4200000,cancel=3980000,taker=5650000` (три, в любом
/// порядке, все обязательны).
impl std::str::FromStr for ExecLatency {
    type Err = anyhow::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let s = s.trim();
        if let Ok(n) = s.parse::<i64>() {
            return Ok(Self::uniform(n));
        }
        let (mut place, mut cancel, mut taker) = (None, None, None);
        for part in s.split(',') {
            let (k, v) = part.split_once('=').ok_or_else(|| {
                anyhow::anyhow!("ожидалось число или place=..,cancel=..,taker=.., получено {s}")
            })?;
            let v: i64 = v.trim().parse().map_err(|e| anyhow::anyhow!("{k}: {e}"))?;
            match k.trim() {
                "place" => place = Some(v),
                "cancel" => cancel = Some(v),
                "taker" => taker = Some(v),
                other => return Err(anyhow::anyhow!("неизвестный ключ задержки {other}")),
            }
        }
        match (place, cancel, taker) {
            (Some(place_ns), Some(cancel_ns), Some(taker_ns)) => Ok(Self {
                place_ns,
                cancel_ns,
                taker_ns,
            }),
            _ => Err(anyhow::anyhow!(
                "нужны все три: place, cancel, taker — получено {s}"
            )),
        }
    }
}

impl std::fmt::Display for ExecLatency {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.is_uniform() {
            write!(f, "{}", self.place_ns)
        } else {
            write!(
                f,
                "place={},cancel={},taker={}",
                self.place_ns, self.cancel_ns, self.taker_ns
            )
        }
    }
}

/// Модель задержки крейта по типу запроса: снятие (`req == Canceled`) —
/// `cancel_ns`, рыночный — `taker_ns`, остальное (лимитка) — `place_ns`.
/// Так крейт помечает запросы сам (`proc/local.rs`: `order.req = Canceled`
/// перед `entry`, у новых — `New` и `order_type`), это не наша разметка.
#[derive(Debug, Clone, Copy)]
pub struct MeasuredLatency(pub ExecLatency);

impl LatencyModel for MeasuredLatency {
    fn entry(&mut self, _timestamp: i64, order: &Order) -> i64 {
        let rtt = if order.req == Status::Canceled {
            self.0.cancel_ns
        } else if order.order_type == OrdType::Market {
            self.0.taker_ns
        } else {
            self.0.place_ns
        };
        latency_from_rtt(rtt).0
    }

    fn response(&mut self, _timestamp: i64, order: &Order) -> i64 {
        let rtt = if order.req == Status::Canceled {
            self.0.cancel_ns
        } else if order.order_type == OrdType::Market {
            self.0.taker_ns
        } else {
            self.0.place_ns
        };
        latency_from_rtt(rtt).1
    }
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

/// Модель очереди и модель исполнения бэктестера (F3 плана 2026-09-20):
/// выбор крейта `hftbacktest 0.9.4` — модель очереди и `ExchangeKind` за ней.
///
/// Три пути исполнения крейта и их оптимизм — в doc-комментарии
/// `build_backtest`; счётчик кругов по пути (3) — `Fill::fill_by_cross`
/// (`n_fill_by_cross` в `forms.csv`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum QueueModelKind {
    /// Прежний движок Decision 20: `RiskAdverseQueueModel` +
    /// `NoPartialFillExchange`. Позиция в очереди двигается **только**
    /// сделками на нашей цене, исполнение — целой заявкой. Числа прежних
    /// прогонов (в том числе `qty` круга) не меняются — гейт F3.
    RiskAdverse,
    /// Модель очереди **по объёму**: `ProbQueueModel<PowerProbQueueFunc(n)>` +
    /// `PartialFillExchange`. Позиция в очереди двигается сделками и
    /// снятиями впереди с вероятностью `P = f(back) / (f(back) + f(front))`,
    /// `f(x) = xⁿ`; исполнение частичное. `n` — число предрегистрации
    /// (`--queue-model prob:<n>`); у крейта в примерах 3.0 — это **не** наше
    /// умолчание, умолчания нет вовсе.
    Prob { n: f64 },
}

/// Ёмкость буфера последних сделок крейта на шаг опроса, элементов (F3):
/// `last_trades_capacity`. Это **не** число сделки и не модельная величина, а
/// подсказка `Vec::with_capacity` — буфер растёт при необходимости; ёмкость
/// обязана быть больше нуля, иначе крейт вовсе не пишет сделки
/// (`proc/local.rs`: `ev.is(LOCAL_TRADE_EVENT) && self.trades.capacity() > 0`)
/// и путь исполнения (3) не определить. Читают буфер двое, оба — на одном
/// шаге и до очистки: детектор `run_round` (путь исполнения F3) и
/// `StrategyState::observe_wall_trades` (F7, Б-75 — съеденное в стену);
/// сразу после чтения буфер очищается.
const LAST_TRADES_CAPACITY: usize = 64;

impl QueueModelKind {
    /// Имя модели для шапки `forms.csv`/`manifest.txt` — обратная запись
    /// флага: `risk-adverse` или `prob:<n>`.
    pub fn label(self) -> String {
        match self {
            QueueModelKind::RiskAdverse => "risk-adverse".to_string(),
            QueueModelKind::Prob { n } => format!("prob:{n}"),
        }
    }

    /// Разбор значения флага `--queue-model`: `risk-adverse` | `prob:<n>`.
    /// `n` — конечное положительное число (домен степенной функции очереди);
    /// умолчания нет — изобретённое число запрещено, `n` приходит флагом.
    pub fn parse(s: &str) -> Result<Self, String> {
        let s = s.trim();
        if s == "risk-adverse" {
            return Ok(QueueModelKind::RiskAdverse);
        }
        let Some(rest) = s.strip_prefix("prob:") else {
            return Err(format!(
                "--queue-model {s:?}: ожидается risk-adverse или prob:<n>"
            ));
        };
        let n: f64 = rest
            .parse()
            .map_err(|_| format!("--queue-model {s:?}: n не число"))?;
        if !n.is_finite() || n <= 0.0 {
            return Err(format!(
                "--queue-model {s:?}: n обязано быть конечным и больше нуля"
            ));
        }
        Ok(QueueModelKind::Prob { n })
    }
}

/// `--queue-model` разбирается `clap`'ом прямо в тип (аудит 21.09, С8) — как
/// `ExecLatency`; текст ошибки — тот же, что у `parse`.
impl std::str::FromStr for QueueModelKind {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::parse(s)
    }
}

/// Настройки прогона профиля.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DriveConfig {
    /// Размер круга. План: минимальный лот биржи (Decision 22).
    pub order_qty: f64,
    /// Первый идентификатор ордеров; дальше — по порядку, каждый ордер
    /// уникален (требование трейта).
    pub first_order_id: u64,
    /// Модель очереди/исполнения этого прогона (F3): её ставит вызывающий
    /// (`lob bounce-grid --queue-model`), умолчания нет.
    pub queue_model: QueueModelKind,
    /// Пропуск «позиция занята» (T-31): `true` — прежнее поведение, сигнал до `idle_ns` прошлого круга —
    /// промах `PositionBusy`. `false` (`bounce-grid --busy-skip off`, только драйвер сетапов, где круг —
    /// свежий движок над окном `t0`): каждый сигнал — свой круг, обрыв суток счёт не прерывает, а след
    /// каждого шага (`BounceRun::trace`) уходит в `signals.csv` — занятость потом решает фильтр
    /// (`tools/compute/busy-replay.py`) тем же правилом.
    pub busy_skip: bool,
    /// Пропуск пустых шагов опроса в удержании (Э-04б, T-38, `bounce-grid --hold-step skip`): в фазе
    /// `Holding` без заявок круг ждёт следующего события ленты (`wait_next_feed`) и встаёт на ту же точку
    /// сетки `ON_EVENT_POLL_STEP_NS`, что и пошаговый опрос; пороги времени плана (досрочный выход, дедлайн)
    /// посещаются. Итог байт в байт тот же, что у `false` (прежний шаг 10 мс на каждой точке сетки).
    /// Только драйвер окон (знает конец данных круга); полный прогон суток шагает как прежде.
    pub hold_skip: bool,
}

/// Чем кончился шаг драйвера на сигнале (след T-31, `signals.csv`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TraceStep {
    /// Книга не была готова — попытки не было (`SignalStep::NotSubmitted`).
    NotSubmitted,
    /// Вход не исполнился и снят (TTL, стена, цена ушла, отказ биржи).
    TimedOut,
    Filled,
    /// Круг не сошёлся (`RoundOutcome::Inconsistent`).
    Inconsistent,
    /// Запись кончилась: при `busy_skip` здесь сутки обрываются.
    EndOfData,
    /// Окна для `t0` нет (драйвер сетапов): при `busy_skip` здесь сутки обрываются.
    NoWindow,
}

impl TraceStep {
    pub fn label(self) -> &'static str {
        match self {
            TraceStep::NotSubmitted => "not_submitted",
            TraceStep::TimedOut => "timed_out",
            TraceStep::Filled => "filled",
            TraceStep::Inconsistent => "inconsistent",
            TraceStep::EndOfData => "end_of_data",
            TraceStep::NoWindow => "no_window",
        }
    }
}

/// След одного сигнала (T-31): всё, что нужно, чтобы вне движка повторить правило «занято» —
/// занят при `t0 < idle_ns` прошлого принятого шага; `EndOfData`, `NoWindow` или остаток, который закрывали
/// до конца записи (`residual_ended`), обрывают сутки.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SignalTrace {
    /// Номер сигнала в порядке по `t0` (как `fill_signal` и `signal_index` в `rounds.csv`).
    pub signal: usize,
    pub step: TraceStep,
    /// Часы, когда стратегия снова `Idle`; `i64::MIN` — шаг без движка (`NoWindow`, `EndOfData` до `t0`).
    pub idle_ns: i64,
    /// Остаток закрывали по рынку страховкой; `Some(true)` — запись кончилась в процессе.
    pub residual: Option<bool>,
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
        if sig.t0_ns > now {
            if bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
                incomplete = true;
                break;
            }
            // Сделки до постановки входа к пути исполнения не относятся
            // (F3): буфер чистится, чтобы детектор `run_round` видел только
            // сделки шага, в котором нога исполнилась.
            bot.clear_last_trades(Some(asset_no));
        }
        // Двойная проверка занятости (как у `blocked_until_ns` выше): по
        // факту тоже, на случай если позиция открыта извне драйвера.
        if bot.position(asset_no) != 0.0 {
            misses.record(MissReason::PositionBusy);
            observations.push(miss_observation(sig.t0_ns));
            continue;
        }

        let mut state = StrategyState::with_plan(
            asset_no,
            sig.sigma,
            cfg.order_qty,
            next_id,
            TradePlan::SpreadHold,
        );
        // Запас номеров на круг — `ID_STRIDE`: страховка от коллизии id со
        // следующим кругом, не экономическая величина.
        next_id = next_id.saturating_add(ID_STRIDE);

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

        // Остаток позиции здесь не страхуется: круг Decision 20 — одна нога
        // входа и одна нога выхода, и `Bot::position` на этом плане честен.
        let (outcome, _residual) = run_round(bot, asset_no, &mut state, entry_id, 1, side, None)?;
        match outcome {
            RoundOutcome::EndOfData => {
                incomplete = true;
                break 'signals;
            }
            RoundOutcome::Inconsistent => incomplete = true,
            RoundOutcome::TimedOut { .. } => {
                misses.record(MissReason::EntryTimeout);
                observations.push(miss_observation(sig.t0_ns));
            }
            RoundOutcome::Filled {
                fill,
                exit_ts,
                reason: _,
                partial: _,
            } => {
                let net = roundtrip_net_bps(&fill);
                observations.push(FillObservation {
                    day_cluster: day_index_ns(sig.t0_ns),
                    net_bps: net.unwrap_or(0.0),
                    filled: net.is_some(),
                });
                fills.push(fill);
                blocked_until_ns = exit_ts;
            }
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

/// Остаток позиции круга со знаком для `flatten_residual` (B4, ревью 23.09):
/// `StrategyState::position()` — объём без знака (`entry − exit ≥ 0`) у обеих
/// сторон, а `flatten_residual` читает знак как сторону позиции (`> 0` — лонг,
/// гасится продажей; `< 0` — шорт, гасится покупкой). Прежде остаток шорта
/// приходил положительным и «гасился» продажей — шорт удваивался вместо
/// закрытия.
fn signed_residual(entry_side: HbtSide, position: f64) -> f64 {
    match entry_side {
        HbtSide::Sell => -position,
        _ => position,
    }
}

enum FlattenOutcome {
    /// Остаток закрыт.
    Flat,
    /// Заявка стала терминальной, а остаток больше нуля (нет ликвидности на
    /// лучшей цене): вызывающий шлёт новую с новым `order_id`.
    Retry {
        left: f64,
    },
    EndOfData,
}

/// Половина шага лота — допуск «объём сошёлся»: дробный выход округляется
/// **вниз** до шага лота (E7), и требовать равенства сумм в `f64` значило бы
/// проверять арифметику, а не позицию.
fn lot_half<MD: MarketDepth>(depth: &MD) -> f64 {
    let lot = depth.lot_size();
    if lot.is_finite() && lot > 0.0 {
        lot * 0.5
    } else {
        0.0
    }
}

/// Закрыть остаток позиции по рынку (IOC) и дождаться ответа биржи.
/// `left` — сколько остатка закрыть, **своей** позиции круга (F4/В-78):
/// `Bot::position` крейта частичного исполнения не видит (обновляется только
/// на `Filled`), и по нему остаток либо теряется, либо остаётся мнимым.
fn flatten_residual<B, MD>(
    bot: &mut B,
    asset_no: usize,
    order_id: u64,
    left: f64,
) -> Result<FlattenOutcome, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let d = bot.depth(asset_no);
    let (bid, ask) = (d.best_bid(), d.best_ask());
    let ok_px = |px: f64| px.is_finite() && px > 0.0;
    if left > 0.0 {
        if !ok_px(bid) {
            return match bot.elapse(ON_EVENT_POLL_STEP_NS)? {
                ElapseResult::EndOfData => Ok(FlattenOutcome::EndOfData),
                _ => Ok(FlattenOutcome::Retry { left }),
            };
        }
        bot.submit_sell_order(
            asset_no,
            order_id,
            bid,
            left,
            TimeInForce::IOC,
            OrdType::Market,
            false,
        )?;
    } else {
        if !ok_px(ask) {
            return match bot.elapse(ON_EVENT_POLL_STEP_NS)? {
                ElapseResult::EndOfData => Ok(FlattenOutcome::EndOfData),
                _ => Ok(FlattenOutcome::Retry { left }),
            };
        }
        bot.submit_buy_order(
            asset_no,
            order_id,
            ask,
            -left,
            TimeInForce::IOC,
            OrdType::Market,
            false,
        )?;
    }
    let half_lot = lot_half(bot.depth(asset_no));
    loop {
        if bot.elapse(ON_EVENT_POLL_STEP_NS)? == ElapseResult::EndOfData {
            return Ok(FlattenOutcome::EndOfData);
        }
        // Готовность — по исполненному объёму заявки (`qty − leaves_qty`), а не
        // по `Bot::position`: частичное исполнение крейт в позиции не видит
        // (находка F3), и после частичного входа она врёт со знаком.
        let Some(o) = bot.orders(asset_no).get(&order_id) else {
            return Ok(FlattenOutcome::Flat);
        };
        let executed = executed_qty(o);
        let resolved = o.req == Status::None
            && !matches!(
                o.status,
                Status::None | Status::New | Status::PartiallyFilled
            );
        if !resolved {
            continue;
        }
        let rest = left.abs() - executed;
        if rest <= half_lot {
            return Ok(FlattenOutcome::Flat);
        }
        return Ok(FlattenOutcome::Retry {
            left: if left > 0.0 { rest } else { -rest },
        });
    }
}

// ---------------------------------------------------------------------------
// Сделка-отскока (В-44, таск 38): план приходит снаружи, исход выхода
// считается по причине.
// ---------------------------------------------------------------------------

/// Сигнал сделки-отскока: момент касания и **готовый план** (В-44). `profile` —
/// номер профиля касания, к которому отнести исход; драйвер не знает, чем
/// профили отличаются, он только считает — это то же разделение, что у
/// `Signal`/`profile_id` старого движка.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BounceSignal {
    pub t0_ns: i64,
    pub sigma: i8,
    pub plan: TradePlan,
    pub profile: u16,
    /// Размер круга этого сигнала (R2, ревью 23.09): лот по цене **сигнала** —
    /// номинал `--order-usd` при цене касания, а не одной ценой на символ
    /// (цена последнего касания записи — заглядывание вперёд: сигнал 1-го
    /// числа получал размер по цене 30-го). `None` — размер прогона
    /// `DriveConfig::order_qty` (явный лот, одиночный `lob backtest`, тесты).
    pub qty: Option<f64>,
}

/// Чем кончились выходы: тейк / стоп / дедлайн / горизонт. У Decision 20
/// причина одна, у В-44 их три, и «сколько раз выбило стопом» — тот самый
/// вопрос практиков о винрейте (D 16:53: 3–4 движения на один стоп).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ExitTally {
    pub take: u64,
    pub stop: u64,
    pub deadline: u64,
    pub horizon: u64,
    /// Трейл-тейк (решение владельца 2026-09-13): выход по откату от лучшего
    /// исхода. Считается отдельно от `take` — иначе не видно, сколько сделок
    /// вытянули больше 1:1 и сколько отдали трейлом.
    pub trail: u64,
    /// Досрочный выход «по прилипанию» (B4, В-58 п. 5): касание не разрешилось
    /// за `X` секунд, уровень остался лучшей ценой. Отдельно от `deadline`:
    /// это свойство касания, а не конец плана.
    pub early: u64,
    /// Выход по съеданию плотности (E5/E7): последняя нога круга ушла по
    /// порогу «съедено ≥ X % от максимума с входа».
    pub eaten: u64,
    /// Кругов с частичным выходом (E7): позиция закрывалась двумя ногами —
    /// доля тейком или по первому порогу съедания, остаток — по плану.
    pub partial: u64,
    /// F7 (Б-75): кругов с выходом «съели» — накопленные сделки в стену ≥ X %.
    pub eaten_by_trades: u64,
    /// F7 (Б-75): кругов с выходом «сняли» — стена упала без сделок.
    pub wall_gone: u64,
}

/// Итог одного профиля касаний: сигналы, круги, промахи (по причинам) и
/// причины выходов. Поля — те же, что нужны CSV таска 38.
#[derive(Debug, Clone, PartialEq)]
pub struct BounceRun {
    pub profile: u16,
    pub signals: u64,
    pub fills: Vec<Fill>,
    /// Индекс сигнала (в порядке прогона) для каждого круга: по нему CLI
    /// относит круг к осям В-44, не гоняя движок по разу на каждую ось.
    pub fill_signal: Vec<usize>,
    /// Причина выхода каждого круга, параллельно `fills`.
    pub fill_reason: Vec<ExitReason>,
    /// Время исполнения выхода каждого круга (нс биржи): из него считается
    /// покрытие суток кругами — доля времени, где биржа вообще нужна.
    pub fill_exit_ns: Vec<i64>,
    pub exits: ExitTally,
    /// Сколько входных ордеров биржа **отвергла** (статус `Rejected`) — прямой
    /// замер вместо догадки о причине неисполнения.
    pub entry_rejected: u64,
    /// Сколько **ног** входа биржа не поставила: пост-онли заявка, пересёкшая
    /// спред, получает `Expired` (GTX в `ack_new`), отвергнутая — `Rejected`
    /// (В-72, F4). Сумма по кругам суток — колонка `n_rejected_postonly`
    /// `forms.csv`; круг без единой поставленной ноги — «сигнал без входа»
    /// (`MissReason::EntryTimeout`), а не «занято».
    pub rejected_postonly: u64,
    /// Сколько раз цена входа в момент отправки **пересекала** спред (для
    /// покупки — `ask ≤ entry_px`): именно эти заявки пост-онли отвергает.
    pub entry_crossed: u64,
    /// Снятия неисполненного входа по потолку срока жизни (F5, В-74) —
    /// колонка `n_entry_cancelled_ttl` `forms.csv`.
    pub entry_cancelled_ttl: u64,
    /// Снятия по «стена снята» (F5, В-74): размер на цене уровня упал ниже
    /// порога В-66 — колонка `n_entry_cancelled_wall_dead`.
    pub entry_cancelled_wall_dead: u64,
    /// Снятия по «цена ушла из полосы лестницы» (F5, В-74) — колонка
    /// `n_entry_cancelled_price_left`.
    pub entry_cancelled_price_left: u64,
    /// F8b (В5/Р2): сколько раз за сутки круг пережил потолок ожидания
    /// подтверждения отмены (`CANCEL_WAIT_NS`) — отдельно по входу
    /// (`EntryCancelReason::CancelTimeout`) и по лимитке выхода. Ноль —
    /// норма: предохранитель не срабатывал, а срабатывание видно в артефактах
    /// (`n_entry_cancelled_cancel_timeout`, `n_exit_cancel_timeout`).
    pub entry_cancelled_cancel_timeout: u64,
    /// Счётчик того же потолка на **выходе** — колонка `n_exit_cancel_timeout`.
    pub exit_cancel_timeout: u64,
    /// F8c (К1): исполнения заявок-сирот (ноги, чьё снятие не подтвердилось
    /// за потолок) — колонка `n_orphan_fills`. Ноль — норма; иначе позиция
    /// на бирже расходилась бы с учётом стратегии, и это должно быть видно.
    pub orphan_fills: u64,
    /// Спред книги в момент отправки входа, в единицах цены — по одному
    /// значению на отправленный вход. Перевод в тики делает вызывающий (тик
    /// знает он, а не движок).
    pub spread_at_entry: Vec<f64>,
    /// Индексы сигналов, по которым вход **отправлен** (заявка ушла): по ним
    /// считается честная доля исполнения `круги / отправленные`, а не
    /// `круги / все касания` — последнее смешивает отказ с пропуском.
    pub submitted_signal: Vec<usize>,
    /// Индексы сигналов, пропущенных как «позиция занята»: по ним вызывающий
    /// считает промахи по строкам осей, а не только итогом.
    pub busy_signal: Vec<usize>,
    /// Самая долгая блокировка позицией, нс: сколько сигнал ждал, пока
    /// освободится. Это и есть ответ на «почему 92 % сигналов пропущено» —
    /// длительность позиции, а не свойство рынка.
    pub busy_wait_ns_max: i64,
    /// Самая долгая жизнь круга (от входа до выхода), нс.
    pub round_ns_max: i64,
    pub misses: MissLedger,
    pub observations: Vec<FillObservation>,
    pub incomplete: bool,
    /// Сколько раз после круга позиция осталась открытой и её пришлось
    /// закрыть по рынку страховкой (2026-09-18). Ноль — норма; каждое
    /// срабатывание печатается и делает прогон `incomplete`.
    pub residual_flattened: u64,
    /// След каждого сигнала — только при `DriveConfig::busy_skip == false` (T-31), иначе пуст.
    pub trace: Vec<SignalTrace>,
}

/// Сколько ног входа ставит этот план: у Decision 20 — одна, у лестницы
/// В-44 — `grid_legs`, у лестницы формы F6 (`ladder.n > 0`) — её ноги
/// (совпавшие тики сложены, так что число ног — уже итоговое). Нужно драйверу,
/// чтобы найти исполненные ноги.
fn legs_of(plan: TradePlan) -> u8 {
    match plan {
        TradePlan::SpreadHold => 1,
        TradePlan::Bounce {
            grid_legs, ladder, ..
        } => {
            if ladder.n > 0 {
                ladder.n
            } else {
                grid_legs.max(1)
            }
        }
    }
}

/// Самая близкая к рынку цена входа плана (F6, В-73): у лестницы формы — её
/// дальняя нога (`to` bps), у прежнего входа — `entry_px`. По ней драйвер
/// отвечает на вопрос «пересекает ли вход спред» (пост-онли биржа такую ногу
/// не ставит, `Expired`), и она же — цена, до которой цена доходит к нашей
/// заявке первой. `None` — план без входа-отскока.
fn entry_market_px(plan: TradePlan) -> Option<f64> {
    match plan {
        TradePlan::SpreadHold => None,
        TradePlan::Bounce {
            entry_px,
            tick_px,
            ladder,
            ..
        } => match ladder.outer_tick() {
            #[allow(clippy::cast_precision_loss)]
            Some(t) => Some(t as f64 * tick_px),
            None => Some(entry_px),
        },
    }
}

/// Итог одного круга в терминах драйвера.
#[derive(Debug, Clone, PartialEq)]
enum RoundOutcome {
    /// Круг закрыт: обе ноги исполнены, причина выхода известна.
    Filled {
        fill: Fill,
        exit_ts: i64,
        /// Причина **последней** ноги выхода.
        reason: ExitReason,
        /// Выход шёл двумя ногами (E7).
        partial: bool,
    },
    /// Вход не исполнился за время жизни плана и снят **либо** ни одной ноги
    /// не поставила биржа (пост-онли заявка пересекла спред, `Expired`, В-72)
    /// — в обоих случаях позиции нет, а у сигнала не было входа. Статус
    /// первого входного ордера несётся наружу: `Rejected` — это отвергнутая
    /// заявка (например, пост-онли, пересекающая спред), а не «просто не
    /// дошло» — разница ровно та, ради которой делается замер. `legs_rejected`
    /// — ног, которых биржа не поставила (счётчик `n_rejected_postonly`).
    TimedOut {
        entry_status: Option<Status>,
        legs_rejected: u8,
        /// Почему вход кончился без позиции (F5, В-74): потолок, смерть стены
        /// или уход цены из полосы; `NotPlaced` — биржа не поставила ни одной
        /// ноги (В-72, «сигнал без входа»).
        reason: EntryCancelReason,
    },
    /// Данные кончились посреди круга.
    EndOfData,
    /// `Idle` без выхода и без таймаута — рассинхрон с данными.
    Inconsistent,
}

/// Может ли хоть одна сделка буфера исполнить эту ногу: для покупки —
/// сделка продавца-агрессора по цене **не выше** нашей (наш лимит впереди
/// неё — приоритет цены), для продажи — зеркально. Пусто — в этом шаге
/// опроса исполнения лентой не было, и нога исполнилась обновлением лучшей
/// цены (путь (3) `build_backtest`).
fn trade_could_fill(trades: &[Event], side: HbtSide, price_tick: i64, tick: f64) -> bool {
    if tick <= 0.0 {
        return false;
    }
    trades.iter().any(|t| {
        let t_tick = (t.px / tick).round() as i64;
        match side {
            HbtSide::Buy => t.is(SELL_EVENT) && t_tick <= price_tick,
            HbtSide::Sell => t.is(BUY_EVENT) && t_tick >= price_tick,
            _ => false,
        }
    })
}

/// Исполненный объём заявки накопленным итогом: у крейта `exec_qty` — объём
/// **последнего** исполнения, а не сумма (частичное исполнение приходит
/// несколькими откликами), поэтому накопленное — `qty − leaves_qty`.
/// `pub(crate)` — тем же счётом живёт позиция круга в стратегии
/// (`lob::strategy`, F4/В-78): частичное исполнение `Bot::position` крейта не
/// видит.
pub(crate) fn executed_qty(order: &Order) -> f64 {
    (order.qty - order.leaves_qty).max(0.0)
}

/// Стоимость исполненного заявки накопленным итогом, `Σ exec_price ×
/// exec_qty` по всем её исполнениям (R7, ревью 23.09). `exec_price()` крейта
/// — цена **последнего** исполнения: рыночный выход, прошедший несколько
/// уровней книги, оценивался бы целиком по худшему уровню, а вход-лимитка,
/// часть которой взяла книгу тейкером, — по цене своего остатка. Итог ведёт
/// биржа модели (вендорная правка `Order::exec_notional`,
/// `docs/findings/hftbacktest-partialfill-fix-2026-09-20.md` §R7). Заявка,
/// чей учёт итога не вёл (нуль при ненулевом исполненном — отклик площадки
/// без этого поля), оценивается прежним `exec_price × исполненное`, а не
/// нулём: живой коннектор обязан вести итог, иначе средняя по свипу снова
/// будет ценой последнего уровня.
pub(crate) fn executed_notional(order: &Order) -> f64 {
    let executed = executed_qty(order);
    if order.exec_notional > 0.0 || executed <= 0.0 {
        order.exec_notional
    } else {
        order.exec_price() * executed
    }
}

/// Отложенный вердикт пути исполнения (F3): для ног из `pending` в буфере
/// последних сделок так и не нашлось сделки, которая могла бы их исполнить, —
/// значит исполнение пришло обновлением лучшей цены (путь (3)
/// `build_backtest`), а не лентой. Откладывается на шаг опроса, потому что
/// сделка в буфере метится **локальным** временем, а исполнение — биржевым:
/// при расхождении меток сделка оказывается в буфере следующим шагом, и
/// мгновенный вердикт звал бы крест там, где была сделка.
fn pending_is_cross<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    side: HbtSide,
    pending: u64,
    legs: u8,
) -> bool
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    if pending == 0 {
        return false;
    }
    let tick = bot.depth(asset_no).tick_size();
    for i in 0..u32::from(legs.max(1)) {
        if i >= u64::BITS {
            break;
        }
        if pending & (1u64 << i) == 0 {
            continue;
        }
        let price_tick = bot
            .orders(asset_no)
            .get(&entry_id.saturating_add(u64::from(i)))
            .map(|o| o.price_tick);
        match price_tick {
            // Заявки уже нет (снята) — судить не по чему: считаем крестом
            // (оценка оптимизма вверх, а не вниз — консервативно для нас).
            None => return true,
            Some(px) => {
                if !trade_could_fill(bot.last_trades(asset_no), side, px, tick) {
                    return true;
                }
            }
        }
    }
    false
}

/// Крутит один круг: продвигает часы стороны шагами `ON_EVENT_POLL_STEP_NS`,
/// пока `on_event` не приведёт состояние к `Idle`, и достаёт из `Bot<MD>`
/// исполнение обеих ног. Общий для обоих планов (`SpreadHold` — Decision 20,
/// `Bounce` — В-44): свой цикл решений у второго плана был бы второй
/// стратегией. Заодно считает путь исполнения входа (F3): крейт отдаёт статус
/// и объём, но не говорит, пришло ли исполнение сделкой или обновлением
/// лучшей цены.
///
/// Возвращает исход круга и **свою** позицию стратегии на его конце (F4,
/// В-78): на ней стоит страховка остатка — `Bot::position` крейта частичного
/// исполнения не видит и после частичного входа врёт со знаком.
/// Пропусков пустых шагов удержания (Э-04б) — счётчик процесса для строки итога суток и тестов; на итог
/// счёта не влияет.
pub static HOLD_SKIPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Событийные шаги при живых заявках (`ALPHA_EVENT_STEPS=1`): между событиями рынка/ответами биржи и
/// таймерами фазы шаг опроса не нужен — решение то же. Умолчание — прежний шаг 10 мс.
fn event_steps() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_EVENT_STEPS").is_some_and(|v| v == "1"))
}

/// Есть ли у круга заявки, ещё живые в крейте (не исполнены целиком, не сняты, не отвергнуты): пока есть,
/// ответы биржи идут по своим часам — пустые шаги опроса не пропускаются (Э-04б).
fn has_open_orders<B, MD>(bot: &B, asset_no: usize) -> bool
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    bot.orders(asset_no)
        .values()
        .any(|o| matches!(o.status, Status::New | Status::PartiallyFilled))
}

/// Шаг удержания с пропуском пустых шагов опроса (Э-04б, T-38). Пошаговый опрос проходит точки
/// `now + k · ON_EVENT_POLL_STEP_NS`; решение удержания без событий рынка меняется только на порогах
/// времени (`hold_wakeup_ns`). Поэтому: ждём следующее событие ленты не дальше первой точки сетки ≥ порога
/// и встаём на первую точку сетки ≥ этого события — крейт к ней обработал ровно те же события в том же
/// порядке, что и пошаговый опрос (`goto` идёт по одной очереди событий, границы шагов её не меняют).
/// `cap` — конец данных круга (меньшая из меток последней строки): дальше последней точки сетки до него
/// не прыгаем, чтобы исчерпание данных (`EndOfData`, часы крейта не двигаются) случилось на том же шаге,
/// что и у опроса.
fn hold_step<B, MD>(
    bot: &mut B,
    wakeup_ns: i64,
    cap: i64,
    include_order_resp: bool,
) -> Result<ElapseResult, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let step = ON_EVENT_POLL_STEP_NS;
    let now = bot.current_timestamp();
    // Точка сетки ≥ порога, не ближе следующей; и последняя точка сетки строго до конца данных.
    let k_wake = wakeup_ns.saturating_sub(now).div_euclid(step)
        + i64::from(wakeup_ns.saturating_sub(now).rem_euclid(step) != 0);
    let k_cap = (cap.saturating_sub(now) - 1).div_euclid(step);
    let k = k_wake.min(k_cap);
    if k <= 1 {
        return bot.elapse(step);
    }
    let target = now.saturating_add(k.saturating_mul(step));
    HOLD_SKIPS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    match bot.wait_next_feed(include_order_resp, target - now)? {
        ElapseResult::MarketFeed | ElapseResult::OrderResponse => {
            let e = bot.current_timestamp();
            let ke = (e - now).div_euclid(step) + i64::from((e - now).rem_euclid(step) != 0);
            let g = now.saturating_add(ke.max(1).saturating_mul(step));
            bot.elapse(g - e)
        }
        other => Ok(other),
    }
}

fn run_round<B, MD>(
    bot: &mut B,
    asset_no: usize,
    state: &mut StrategyState,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
    skip_cap: Option<i64>,
) -> Result<(RoundOutcome, f64), B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut timed_out = false;
    // Почему вход кончился без позиции (F5, В-74): несётся из `on_event` до
    // `RoundOutcome::TimedOut`, чтобы по кругам формы посчитались снятия по
    // стене, полосе и потолку отдельно.
    let mut cancel_reason = EntryCancelReason::NotPlaced;
    // Путь исполнения входа (F3): `entry_seen` — ноги, исполнение которых уже
    // замечено; `entry_pending` — ноги, чей вердикт (сделка или крест) отложен
    // на шаг (локальная метка сделки отстаёт от биржевой); `fill_by_cross` —
    // итог по кругу. Битовая маска вместо вектора: ног у плана единицы.
    let mut entry_seen: u64 = 0;
    let mut entry_pending: u64 = 0;
    let mut fill_by_cross = false;
    // Ноги выхода в порядке отправки: одна у прежних форм, две у дробных (E7),
    // и больше при доборах остатка моделью очереди по объёму (F4). Третий
    // элемент — заявка несла **часть** позиции (`Action::partial`), а не весь
    // остаток: `n_partial` считает именно доли E7, а не число заявок выхода.
    let mut exits: Vec<(u64, ExitReason, bool)> = Vec::new();
    // Э-04б: решение удержания уже посчитано на текущей точке сетки (см. пропуск шагов ниже).
    let mut decided_in_hold = false;
    let ev_steps = event_steps();
    let mut stable = false;
    let skip_on = skip_same();
    let mut solo_sig = SigMemo::default();
    loop {
        // Э-04б: в удержании без заявок пустые шаги опроса пропускаются (`hold_step`), иначе — шаг 10 мс.
        // Только если решение удержания на этой точке сетки уже принято (`decided_in_hold`): шаг, на котором
        // круг **вошёл** в удержание (исполнился вход, вернулся из выхода), решения ещё не считал — первое
        // решение идёт следующим шагом и без событий (гейт 27.09: `gone50`/`eat50x80` на 03.08).
        let wakeup = match skip_cap {
            Some(_) if ev_steps && stable && entry_pending == 0 => {
                state.step_wakeup_ns(bot.current_timestamp())
            }
            Some(_)
                if !ev_steps
                    && decided_in_hold
                    && entry_pending == 0
                    && !has_open_orders(bot, asset_no) =>
            {
                state.hold_wakeup_ns(bot.current_timestamp())
            }
            _ => None,
        };
        let stepped = match (wakeup, skip_cap) {
            (Some(th), Some(cap)) => {
                STEP_KINDS[2].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let resp = !state.is_holding() || has_open_orders(bot, asset_no);
                hold_step(bot, th, cap, resp)?
            }
            _ => {
                let k = usize::from(has_open_orders(bot, asset_no));
                STEP_KINDS[k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                bot.elapse(ON_EVENT_POLL_STEP_NS)?
            }
        };
        if stepped == ElapseResult::EndOfData {
            // Хвост записи: круг неполон, `Fill` не строится — вердикт пути
            // исполнения не нужен.
            return Ok((RoundOutcome::EndOfData, state.position()));
        }
        // Отложенный вердикт: буфер за это время дорос сделками следующего
        // шага — если сделка, способная исполнить ногу, появилась, это лента.
        if entry_pending != 0 {
            fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
            entry_pending = 0;
        }
        for i in 0..u32::from(legs.max(1)) {
            if i >= u64::BITS {
                break;
            }
            let bit = 1u64 << i;
            if entry_seen & bit != 0 {
                continue;
            }
            let Some(o) = bot
                .orders(asset_no)
                .get(&entry_id.saturating_add(u64::from(i)))
            else {
                continue;
            };
            // Исполнено — накопленным (`qty − leaves_qty`), а не `exec_qty`:
            // у крейта `exec_qty` — объём **последнего** исполнения, а не сумма.
            if executed_qty(o) <= 0.0 {
                continue;
            }
            entry_seen |= bit;
            let tick = bot.depth(asset_no).tick_size();
            if !trade_could_fill(bot.last_trades(asset_no), side, o.price_tick, tick) {
                // Сделки, способной исполнить ногу, в буфере пока нет: вердикт
                // ждёт шага, чтобы локальная метка сделки догнала биржевое
                // исполнение. Если сделки не будет и на следующем шаге —
                // путь (3), обновление лучшей цены.
                entry_pending |= bit;
            }
        }
        // Буфер нужен только пока есть отложенный вердикт — иначе очищаем
        // (память не растёт с длиной круга). Перед очисткой сделки этого шага
        // отдаются стратегии: F7 (Б-75) считает по ним съеденное в стену, а
        // заново буфер не открывается — считаем ровно один раз на шаг.
        if entry_pending == 0 {
            state.observe_wall_trades(bot.last_trades(asset_no));
            bot.clear_last_trades(Some(asset_no));
        }
        // Решение удержания принято на этой точке, только если круг был в удержании **до** вызова и остался.
        let held_before = state.hold_wakeup_ns(bot.current_timestamp()).is_some();
        let mark_before = state.phase_mark();
        let skip = skip_on
            && solo_sig.skip(state.hold_input_sig(bot.depth(asset_no), bot.current_timestamp()));
        let action = if skip {
            Action::Idle
        } else {
            on_event(bot, state)?
        };
        if skip_on && !matches!(action, Action::Idle) {
            solo_sig.reset();
        }
        decided_in_hold = held_before && state.hold_wakeup_ns(bot.current_timestamp()).is_some();
        stable = state.phase_mark() == mark_before && matches!(action, Action::Idle);
        match action {
            Action::EntryTimedOut { reason, .. } => {
                timed_out = true;
                cancel_reason = reason;
            }
            Action::ExitSubmitted {
                order_id,
                reason,
                partial,
                ..
            } => exits.push((order_id, reason, partial)),
            Action::Idle | Action::EntrySubmitted { .. } => {}
        }
        if state.is_idle() {
            break;
        }
    }
    fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
    // Буфер сделок очищается и на выходе из круга: сигналы бывают встык
    // (`t0` не двигает часы), и сделки прошлого круга не должны решать вердикт
    // следующего. Сделки последнего шага перед этим отдаются стратегии (F7).
    state.observe_wall_trades(bot.last_trades(asset_no));
    bot.clear_last_trades(Some(asset_no));
    if timed_out {
        let entry_status = bot.orders(asset_no).get(&entry_id).map(|o| o.status);
        return Ok((
            RoundOutcome::TimedOut {
                entry_status,
                legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),
                reason: cancel_reason,
            },
            state.position(),
        ));
    }
    let Some(&(_, reason, _)) = exits.last() else {
        return Ok((RoundOutcome::Inconsistent, state.position()));
    };
    // Исполненные ноги входа. Отбор — **по факту исполнения** (`exec_qty`), а
    // не по статусу `Filled`: модель очереди по объёму (F3) отдаёт ногу
    // частично исполненной (`PartiallyFilled`), а снятая после частичного
    // исполнения нога — `Canceled` с ненулевым `exec_qty`. У прежней модели
    // (`RiskAdverse`) множество то же: там исполнение всегда целое.
    //
    // Цена входа: `entry_px` — средняя с равными весами (как было: у лестницы
    // ноги равного размера), `entry_vwap` — средневзвешенная по исполненному
    // размеру; при полном исполнении они совпадают, и `roundtrip_net_bps`
    // считает по `entry_vwap` (F3).
    let mut entry_px_sum = 0.0;
    let mut entry_notional = 0.0;
    let mut entry_qty = 0.0;
    let mut entry_legs = 0usize;
    let mut entry_taker = false;
    let mut ordered = 0.0;
    for i in 0..u64::from(legs.max(1)) {
        let Some(o) = bot.orders(asset_no).get(&entry_id.saturating_add(i)) else {
            continue;
        };
        // Заказанное — только принятые биржей ноги: отвергнутая (`Rejected`,
        // `Expired`) нога — отказ, а не заказ, и `fill_frac` из-за неё падать
        // не должен (F4 считает её отдельным счётчиком).
        if !matches!(o.status, Status::Rejected | Status::Expired) {
            ordered += o.qty;
        }
        let exec = executed_qty(o);
        if exec > 0.0 {
            entry_qty += exec;
            // R7: средняя цена ноги — по всем её исполнениям, не цена
            // последнего уровня свипа.
            let notional = executed_notional(o);
            entry_px_sum += notional / exec;
            entry_notional += notional;
            entry_legs += 1;
            // Комиссия ноги — по флагу `maker` ордера крейта (В-63): у
            // лестницы вход тейкерский, если тейкером исполнилась хотя бы одна
            // нога (консервативно).
            entry_taker |= !o.maker;
        }
    }
    let entry_px = if entry_legs == 0 {
        None
    } else {
        Some(entry_px_sum / crate::stats::count_f64(entry_legs))
    };
    let entry_vwap = if entry_qty > 0.0 {
        entry_notional / entry_qty
    } else {
        0.0
    };
    let fill_frac = if ordered > 0.0 {
        entry_qty / ordered
    } else {
        0.0
    };
    // Цена выхода — **средневзвешенная по размеру** исполненных ног (у
    // цельного выхода нога одна — это его же цена); время — последней ноги;
    // комиссия выхода — тейкерская, если тейкером ушла хотя бы одна нога
    // (консервативно, как у лестницы входа).
    let mut exit_qty = 0.0;
    let mut exit_notional = 0.0;
    let mut exit_ts = i64::MIN;
    let mut exit_taker = false;
    for (exit_id, _, _) in &exits {
        if let Some(o) = bot
            .orders(asset_no)
            .get(exit_id)
            .filter(|o| executed_qty(o) > 0.0)
        {
            let exec = executed_qty(o);
            exit_qty += exec;
            exit_notional += executed_notional(o);
            exit_ts = exit_ts.max(o.exch_timestamp);
            exit_taker |= !o.maker;
        }
    }
    // Выход состоялся, если исполненный объём **закрыл вход** — с точностью до
    // шага лота (округление дробного выхода, E7). Заявка выхода, не отдавшая
    // ни лота (маркет-выход в пустой книге — `Expired` у модели очереди по
    // объёму), круг не рассыпает: стратегия шлёт следующую на остаток (F4,
    // В-78), и в `exits` такие ноги остаются. Под полным исполнением
    // (`RiskAdverse`) условие совпадает с прежним «все ноги исполнились», так
    // что числа прежних прогонов не меняются.
    let exit_ok = exit_qty > 0.0 && exit_qty + lot_half(bot.depth(asset_no)) >= entry_qty;
    match (entry_px, exit_ok) {
        (Some(entry_px), true) => {
            let dir = if side == HbtSide::Buy { 1 } else { -1 };
            Ok((
                RoundOutcome::Filled {
                    fill: Fill {
                        dir,
                        entry_px,
                        exit_px: exit_notional / exit_qty,
                        qty: entry_qty,
                        entry_taker,
                        exit_taker,
                        entry_vwap,
                        fill_frac,
                        legs_filled: u8::try_from(entry_legs).unwrap_or(u8::MAX),
                        legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),
                        fill_by_cross,
                    },
                    exit_ts,
                    reason,
                    // Доля отдана, если хоть одна заявка выхода несла часть
                    // позиции (E7). Число заявок о доле не говорит: остаток
                    // добирается новыми заявками (F4).
                    partial: exits.iter().any(|(_, _, partial)| *partial),
                },
                state.position(),
            ))
        }
        _ => Ok((RoundOutcome::Inconsistent, state.position())),
    }
}

/// Ноги входа, которых биржа **не поставила**: пост-онли заявка (GTX),
/// пересёкшая спред, получает `Expired` в `ack_new` (В-72), отвергнутая —
/// `Rejected`. Такая нога — отказ, а не заказ: в `fill_frac` она не входит,
/// её считает `legs_rejected` (`n_rejected_postonly` в `forms.csv`).
fn rejected_legs<B, MD>(bot: &B, asset_no: usize, entry_id: u64, legs: u8) -> u8
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut n = 0u8;
    for i in 0..u64::from(legs.max(1)) {
        if bot
            .orders(asset_no)
            .get(&entry_id.saturating_add(i))
            .is_some_and(|o| matches!(o.status, Status::Rejected | Status::Expired))
        {
            n = n.saturating_add(1);
        }
    }
    n
}

// ---------------------------------------------------------------------------
// Э-08 (T-38, «один проход на вход»): группа форм с общим входом, свой выход
// на клон — вход разворачивается движком один раз, а не по разу на форму.
// ---------------------------------------------------------------------------

/// Итог ноги входа общего для группы круга — те же поля и та же формула, что
/// у хвоста сольного `run_round` (R7/В-78, комментарии там же), вынесены сюда,
/// чтобы посчитать один раз на К вариантов, а не по разу на каждый.
/// `None` — ни одна нога не исполнилась (Inconsistent для всех вариантов).
///
/// Раздел Э-08 вызывается `bounce-grid --exit-group on` через `precompute_exit_group` (групповые круги
/// кладутся в память кругов формы, дальше обычный драйвер берёт их оттуда).
struct EntryTally {
    entry_px: f64,
    entry_vwap: f64,
    entry_qty: f64,
    fill_frac: f64,
    entry_legs: usize,
    entry_taker: bool,
}

fn entry_tally<B, MD>(bot: &B, asset_no: usize, entry_id: u64, legs: u8) -> Option<EntryTally>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut entry_px_sum = 0.0;
    let mut entry_notional = 0.0;
    let mut entry_qty = 0.0;
    let mut entry_legs = 0usize;
    let mut entry_taker = false;
    let mut ordered = 0.0;
    for i in 0..u64::from(legs.max(1)) {
        let Some(o) = bot.orders(asset_no).get(&entry_id.saturating_add(i)) else {
            continue;
        };
        if !matches!(o.status, Status::Rejected | Status::Expired) {
            ordered += o.qty;
        }
        let exec = executed_qty(o);
        if exec > 0.0 {
            entry_qty += exec;
            let notional = executed_notional(o);
            entry_px_sum += notional / exec;
            entry_notional += notional;
            entry_legs += 1;
            entry_taker |= !o.maker;
        }
    }
    if entry_legs == 0 {
        return None;
    }
    Some(EntryTally {
        entry_px: entry_px_sum / crate::stats::count_f64(entry_legs),
        entry_vwap: if entry_qty > 0.0 {
            entry_notional / entry_qty
        } else {
            0.0
        },
        entry_qty,
        fill_frac: if ordered > 0.0 {
            entry_qty / ordered
        } else {
            0.0
        },
        entry_legs,
        entry_taker,
    })
}

/// Хвост одного варианта группы: своя нога выхода (`exits`), общая нога входа
/// (`entry`) — та же сборка `Fill`, что у сольного `run_round` (только выход
/// у каждого варианта свой).
#[allow(clippy::too_many_arguments)]
fn build_group_outcome<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
    fill_by_cross: bool,
    entry: &EntryTally,
    exits: &[(u64, ExitReason, bool)],
) -> RoundOutcome
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let Some(&(_, reason, _)) = exits.last() else {
        return RoundOutcome::Inconsistent;
    };
    let mut exit_qty = 0.0;
    let mut exit_notional = 0.0;
    let mut exit_ts = i64::MIN;
    let mut exit_taker = false;
    for (exit_id, _, _) in exits {
        if let Some(o) = bot
            .orders(asset_no)
            .get(exit_id)
            .filter(|o| executed_qty(o) > 0.0)
        {
            let exec = executed_qty(o);
            exit_qty += exec;
            exit_notional += executed_notional(o);
            exit_ts = exit_ts.max(o.exch_timestamp);
            exit_taker |= !o.maker;
        }
    }
    let exit_ok = exit_qty > 0.0 && exit_qty + lot_half(bot.depth(asset_no)) >= entry.entry_qty;
    if !exit_ok {
        return RoundOutcome::Inconsistent;
    }
    let dir = if side == HbtSide::Buy { 1 } else { -1 };
    RoundOutcome::Filled {
        fill: Fill {
            dir,
            entry_px: entry.entry_px,
            exit_px: exit_notional / exit_qty,
            qty: entry.entry_qty,
            entry_taker: entry.entry_taker,
            exit_taker,
            entry_vwap: entry.entry_vwap,
            fill_frac: entry.fill_frac,
            legs_filled: u8::try_from(entry.entry_legs).unwrap_or(u8::MAX),
            legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),
            fill_by_cross,
        },
        exit_ts,
        reason,
        partial: exits.iter().any(|(_, _, partial)| *partial),
    }
}

/// Круг группы форм (Э-08): общий вход разворачивается один раз (`entry_state`, уже в
/// `EntryPending` — вызывающий отправил его как в сольном `drive_signal`), а после форка
/// (переход в `Holding`) каждый вариант ведёт свой выход своими заявками (`variant_next_ids` —
/// непересекающиеся диапазоны номеров) в том же движке до своего `Idle`. Точно: наши заявки не
/// двигают глубину модели очереди (`queue_model.trade` не декрементирует объём между заявками,
/// `partialfillexchange.rs` крейта) — К вариантов на одном движке эквивалентны К сольным прогонам
/// с тем же входом (тикет Э-08, обоснование там же).
///
/// Без пропуска пустых шагов удержания (Э-04б): группа всегда шагает прежним шагом
/// `ON_EVENT_POLL_STEP_NS`. Совместный пропуск потребовал бы порога — минимума по всем вариантам —
/// и «есть открытые заявки» отдельно на клон (`has_open_orders` сейчас смотрит на все заявки
/// актива разом, не различая, чьи они), что в бюджет этой задачи не вошло: `--exit-group on` не
/// снижает эффект `--hold-step skip` там, где применимо (гейт — `group_matches_solo_with_hold_skip`
/// в тестах), но и не пользуется им внутри группы.
///
/// Возвращает исход и позицию плана на конце круга по каждому варианту, в порядке `variant_plans`.
#[allow(clippy::too_many_arguments)]
fn run_round_group<B, MD>(
    bot: &mut B,
    asset_no: usize,
    entry_state: &mut StrategyState,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
    variant_plans: &[TradePlan],
    variant_next_ids: &[u64],
    skip_cap: Option<i64>,
) -> Result<Vec<GroupRoundResult>, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    debug_assert_eq!(variant_plans.len(), variant_next_ids.len());
    // Круг кончился без форка (конец записи/таймаут входа) — исход, позиция, счётчики F8b/F8c и
    // сироты общего входа одни на всю группу: форк ещё не случился, у вариантов своей истории нет.
    let broadcast = |entry_state: &mut StrategyState,
                     outcome: RoundOutcome,
                     n: usize,
                     idle_ns: i64|
     -> Vec<GroupRoundResult> {
        let tail = (
            entry_state.position(),
            entry_state.exit_cancel_timeouts(),
            entry_state.orphan_fills(),
            entry_state.take_orphans(),
        );
        std::iter::repeat_with(move || GroupRoundResult {
            outcome: outcome.clone(),
            position: tail.0,
            exit_cancel_timeouts: tail.1,
            orphan_fills: tail.2,
            idle_ns,
            carry_out: tail.3,
        })
        .take(n)
        .collect()
    };
    // --- общий вход: тот же цикл, что у сольного `run_round`, до `Holding` либо до конца круга
    // без входа (таймаут/конец записи) — тогда исход один на всю группу. ---
    let mut timed_out = false;
    let mut cancel_reason = EntryCancelReason::NotPlaced;
    let mut entry_seen: u64 = 0;
    let mut entry_pending: u64 = 0;
    let mut fill_by_cross = false;
    let ev_steps = event_steps();
    let mut entry_stable = false;
    loop {
        let entry_wake = match skip_cap {
            Some(_) if ev_steps && entry_stable && entry_pending == 0 => {
                entry_state.step_wakeup_ns(bot.current_timestamp())
            }
            _ => None,
        };
        let entry_stepped = match (entry_wake, skip_cap) {
            (Some(th), Some(cap)) => {
                let resp = !entry_state.is_holding() || has_open_orders(bot, asset_no);
                hold_step(bot, th, cap, resp)?
            }
            _ => bot.elapse(ON_EVENT_POLL_STEP_NS)?,
        };
        if entry_stepped == ElapseResult::EndOfData {
            let now = bot.current_timestamp();
            return Ok(broadcast(
                entry_state,
                RoundOutcome::EndOfData,
                variant_plans.len(),
                now,
            ));
        }
        if entry_pending != 0 {
            fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
            entry_pending = 0;
        }
        for i in 0..u32::from(legs.max(1)) {
            if i >= u64::BITS {
                break;
            }
            let bit = 1u64 << i;
            if entry_seen & bit != 0 {
                continue;
            }
            let Some(o) = bot
                .orders(asset_no)
                .get(&entry_id.saturating_add(u64::from(i)))
            else {
                continue;
            };
            if executed_qty(o) <= 0.0 {
                continue;
            }
            entry_seen |= bit;
            let tick = bot.depth(asset_no).tick_size();
            if !trade_could_fill(bot.last_trades(asset_no), side, o.price_tick, tick) {
                entry_pending |= bit;
            }
        }
        if entry_pending == 0 {
            entry_state.observe_wall_trades(bot.last_trades(asset_no));
            bot.clear_last_trades(Some(asset_no));
        }
        let entry_mark = entry_state.phase_mark();
        let entry_action = on_event(bot, entry_state)?;
        entry_stable =
            entry_state.phase_mark() == entry_mark && matches!(entry_action, Action::Idle);
        match entry_action {
            Action::EntryTimedOut { reason, .. } => {
                timed_out = true;
                cancel_reason = reason;
            }
            Action::Idle | Action::EntrySubmitted { .. } => {}
            Action::ExitSubmitted { .. } => {
                // Общий вход `on_event` не выходит — план начал бы держать позицию только у
                // одиночного `Bounce`/`SpreadHold` (`decide_exit` читает `Phase::Holding`, форк
                // сюда не доходит): если это случилось, дальше по группе идти нельзя.
                unreachable!("общий вход круга группы не выходит — форк раньше, в Holding")
            }
        }
        if entry_state.is_idle() {
            debug_assert!(timed_out, "вход решился Idle не через таймаут");
            let entry_status = bot.orders(asset_no).get(&entry_id).map(|o| o.status);
            let outcome = RoundOutcome::TimedOut {
                entry_status,
                legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),
                reason: cancel_reason,
            };
            let now = bot.current_timestamp();
            return Ok(broadcast(entry_state, outcome, variant_plans.len(), now));
        }
        if entry_state.is_holding() {
            break;
        }
    }
    // --- форк: К клонов состояния входа, каждому — свой план (выход) и свой диапазон заявок. ---
    let mut states: Vec<StrategyState> = variant_plans
        .iter()
        .zip(variant_next_ids)
        .map(|(&plan, &id)| {
            let mut s = entry_state.clone();
            s.set_plan(plan);
            s.set_next_order_id(id);
            s
        })
        .collect();
    let n = states.len();
    let mut exits: Vec<Vec<(u64, ExitReason, bool)>> = vec![Vec::new(); n];
    let mut outcome: Vec<Option<RoundOutcome>> = vec![None; n];
    // Своя метка часов на клон — в момент, когда **этот** вариант вернулся в `Idle`, а не после
    // общего цикла (движок группы шагает, пока не решатся все К вариантов; вариант, решившийся
    // раньше соседей, иначе унаследовал бы их более позднюю метку — `SignalStep::idle_ns` тогда
    // разошёлся бы с сольным кругом того же варианта, находка теста при разработке Э-08).
    let mut idle_ns: Vec<i64> = vec![0; n];
    // Э-04б в группе (условие Судьи 1 к Э-08): пропуск пустых шагов — только если у **всех** живых
    // вариантов решение удержания уже принято на этой точке (`decided`), заявок в движке нет, а порог —
    // минимум по вариантам. После форка первое решение ещё не считано (как у сольного круга).
    let mut decided: Vec<bool> = vec![false; n];
    let mut stable: Vec<bool> = vec![false; n];
    let skip_on = skip_same();
    let mut sigs: Vec<SigMemo> = vec![SigMemo::default(); n];
    loop {
        if outcome.iter().all(Option::is_some) {
            break;
        }
        let wakeup = match skip_cap {
            Some(_) if ev_steps && entry_pending == 0 => {
                let now = bot.current_timestamp();
                let mut th: Option<i64> = None;
                let mut all = true;
                for i in 0..n {
                    if outcome[i].is_some() {
                        continue;
                    }
                    match (stable[i], states[i].step_wakeup_ns(now)) {
                        (true, Some(t)) => th = Some(th.map_or(t, |x: i64| x.min(t))),
                        _ => {
                            all = false;
                            break;
                        }
                    }
                }
                th.filter(|_| all)
            }
            Some(_) if !ev_steps && !has_open_orders(bot, asset_no) => {
                let now = bot.current_timestamp();
                let mut th: Option<i64> = None;
                let mut all = true;
                for i in 0..n {
                    if outcome[i].is_some() {
                        continue;
                    }
                    match (decided[i], states[i].hold_wakeup_ns(now)) {
                        (true, Some(t)) => th = Some(th.map_or(t, |x: i64| x.min(t))),
                        _ => {
                            all = false;
                            break;
                        }
                    }
                }
                th.filter(|_| all)
            }
            _ => None,
        };
        let stepped = match (wakeup, skip_cap) {
            (Some(th), Some(cap)) => {
                let resp = has_open_orders(bot, asset_no)
                    || states
                        .iter()
                        .zip(&outcome)
                        .any(|(s, o)| o.is_none() && !s.is_holding());
                hold_step(bot, th, cap, resp)?
            }
            _ => bot.elapse(ON_EVENT_POLL_STEP_NS)?,
        };
        if stepped == ElapseResult::EndOfData {
            let now = bot.current_timestamp();
            for (o, t) in outcome.iter_mut().zip(idle_ns.iter_mut()) {
                if o.is_none() {
                    *o = Some(RoundOutcome::EndOfData);
                    *t = now;
                }
            }
            break;
        }
        // Путь исполнения входа (F3) — как у сольного `run_round` на каждом шаге и после форка:
        // отложенный вердикт ноги и ноги лестницы, исполнившиеся уже в удержании.
        if entry_pending != 0 {
            fill_by_cross |= pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs);
            entry_pending = 0;
        }
        for i in 0..u32::from(legs.max(1)) {
            if i >= u64::BITS {
                break;
            }
            let bit = 1u64 << i;
            if entry_seen & bit != 0 {
                continue;
            }
            let Some(o) = bot
                .orders(asset_no)
                .get(&entry_id.saturating_add(u64::from(i)))
            else {
                continue;
            };
            if executed_qty(o) <= 0.0 {
                continue;
            }
            entry_seen |= bit;
            let tick = bot.depth(asset_no).tick_size();
            if !trade_could_fill(bot.last_trades(asset_no), side, o.price_tick, tick) {
                entry_pending |= bit;
            }
        }
        // F7 (Б-75): все живые варианты видят сделки этого шага **до** общей очистки буфера —
        // как у сольного `run_round`, только К раз вместо одного (и так же откладывается, пока
        // вердикт ноги входа ждёт шага).
        if entry_pending == 0 {
            for (i, s) in states.iter_mut().enumerate() {
                if outcome[i].is_none() {
                    s.observe_wall_trades(bot.last_trades(asset_no));
                }
            }
            bot.clear_last_trades(Some(asset_no));
        }
        for i in 0..n {
            if outcome[i].is_some() {
                continue;
            }
            let held_before = states[i].hold_wakeup_ns(bot.current_timestamp()).is_some();
            let mark_before = states[i].phase_mark();
            let skip = skip_on
                && sigs[i]
                    .skip(states[i].hold_input_sig(bot.depth(asset_no), bot.current_timestamp()));
            let act = if skip {
                Action::Idle
            } else {
                on_event(bot, &mut states[i])?
            };
            if skip_on && !matches!(act, Action::Idle) {
                sigs[i].reset();
            }
            stable[i] = states[i].phase_mark() == mark_before && matches!(act, Action::Idle);
            match act {
                Action::ExitSubmitted {
                    order_id,
                    reason,
                    partial,
                    ..
                } => exits[i].push((order_id, reason, partial)),
                Action::Idle | Action::EntrySubmitted { .. } | Action::EntryTimedOut { .. } => {}
            }
            decided[i] = held_before && states[i].hold_wakeup_ns(bot.current_timestamp()).is_some();
            if states[i].is_idle() {
                idle_ns[i] = bot.current_timestamp();
                // Сольный круг на своём конце добирает отложенный вердикт ноги входа.
                let fbc = fill_by_cross
                    || (entry_pending != 0
                        && pending_is_cross(bot, asset_no, entry_id, side, entry_pending, legs));
                outcome[i] = Some(match entry_tally(bot, asset_no, entry_id, legs) {
                    Some(entry) => build_group_outcome(
                        bot, asset_no, entry_id, legs, side, fbc, &entry, &exits[i],
                    ),
                    None => RoundOutcome::Inconsistent,
                });
            }
        }
    }
    Ok(outcome
        .into_iter()
        .zip(states)
        .zip(idle_ns)
        .map(|((o, mut s), t)| GroupRoundResult {
            outcome: o.unwrap_or(RoundOutcome::Inconsistent),
            position: s.position(),
            exit_cancel_timeouts: s.exit_cancel_timeouts(),
            orphan_fills: s.orphan_fills(),
            carry_out: s.take_orphans(),
            idle_ns: t,
        })
        .collect())
}

/// Итог одного варианта группового круга (Э-08): то же, что сольный `drive_signal` берёт из
/// `RoundOutcome` и `StrategyState` напрямую, — позиция для страховки остатка, счётчики F8b/F8c и
/// сироты, унаследуемые следующим сигналом этого же варианта.
struct GroupRoundResult {
    outcome: RoundOutcome,
    /// Своя позиция плана на конце круга (F4, В-78) — по ней страховка остатка сигнала.
    position: f64,
    exit_cancel_timeouts: u64,
    orphan_fills: u64,
    carry_out: OrphanCarry,
    /// Часы стороны в момент, когда **этот** вариант вернулся в `Idle` (`SignalStep::idle_ns` у
    /// сольного круга) — своя метка на клон, взятая **в момент форка/исполнения именно его**, а
    /// не после общего цикла: движок группы шагает, пока не решатся все К вариантов, и вариант,
    /// решившийся раньше соседей, не должен получить их более позднюю метку (иначе `idle_ns`,
    /// а с ним и `signals.csv`, разошлись бы с сольным кругом того же варианта — находка теста
    /// `group_round_matches_three_solo_runs_with_divergent_exits`, разбор Э-08).
    idle_ns: i64,
}

/// Отсчёт заявок общего круга группы (Э-08) — отдельное от `--first-order-id` (умолчание `1`)
/// пространство: сирота, унаследованная из группового круга, никогда не совпадёт номером с
/// заявкой сольного круга того же варианта (`drive_signal`), даже если сироту не заметить и
/// вариант потом поведут сольно (соседний сигнал без группы — не пустые сироты, T-38). Место —
/// `u64` половина, нет практического исчерпания на сутки сигналов.
const GROUP_ID_BASE: u64 = 1 << 40;

/// То же, что `drive_signal`, но для К вариантов одной группы форм (Э-08, «один проход на
/// вход»): вход общий, разворачивается один раз (по `variant_plans[0]` — входные поля у всех
/// вариантов группы равны по построению, проверка вызывающего, `drive_bounce_windowed_group`), а
/// выход у каждого варианта свой (`run_round_group`). `sig.plan` не читается — сигнал несёт
/// только `t0_ns`/`sigma`/`qty`, план входа берётся из `variant_plans[0]` напрямую.
///
/// `variant_carries` — сироты, унаследованные каждым вариантом от **прошлого** сигнала: группа
/// безопасна только когда они у всех пусты (иначе общий вход не может ответить, чья это сирота, —
/// проверка вызывающего, здесь только `debug_assert`). Номера заявок — свой независимый счётчик
/// (`GROUP_ID_BASE`), а не `variant_next_ids`/`cfg.first_order_id` сольного пути: движок сигнала
/// свежий (`windowed_with`), поэтому единственное, что должно быть монотонно, — сироты одного и
/// того же варианта между сигналами, а не пересечение чисел с сольным путём (см. `GROUP_ID_BASE`).
#[allow(clippy::too_many_arguments)]
fn drive_signal_group<B, MD>(
    bot: &mut B,
    asset_no: usize,
    sig: &BounceSignal,
    variant_plans: &[TradePlan],
    cfg: &DriveConfig,
    next_group_id: &mut u64,
    variant_carries: &mut [OrphanCarry],
    data_end_ns: Option<i64>,
) -> Result<Vec<SignalStep>, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let n = variant_plans.len();
    debug_assert_eq!(variant_carries.len(), n);
    debug_assert!(
        variant_carries.iter().all(OrphanCarry::is_empty),
        "группа форм (Э-08) требует пустых сирот у всех вариантов — проверка вызывающего"
    );
    let now = bot.current_timestamp();
    if sig.t0_ns > now {
        if bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
            return Ok(vec![SignalStep::EndOfData; n]);
        }
        bot.clear_last_trades(Some(asset_no));
    }
    let qty = sig.qty.unwrap_or(cfg.order_qty);
    debug_assert!(qty > 0.0, "размер круга обязан быть положительным: {qty}");
    let entry_id_base = *next_group_id;
    let mut entry_state =
        StrategyState::with_plan(asset_no, sig.sigma, qty, entry_id_base, variant_plans[0]);
    let (entry_id, side) = match on_event(bot, &mut entry_state)? {
        Action::EntrySubmitted { order_id, side, .. } => (order_id, side),
        _ => {
            *next_group_id = entry_id_base.saturating_add(ID_STRIDE);
            let idle_ns = bot.current_timestamp();
            return Ok(vec![SignalStep::NotSubmitted { idle_ns }; n]);
        }
    };
    // Замер механизма отказа (таск 38), как у сольного `drive_signal` — по представителю группы:
    // входные поля (в т.ч. `entry_px`/лестница) у всех вариантов равны по построению.
    let mut crossed = false;
    let mut spread = None;
    if let Some(entry_px) = entry_market_px(variant_plans[0]) {
        let d = bot.depth(asset_no);
        let (bid, ask) = (d.best_bid(), d.best_ask());
        if bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0 {
            crossed = match side {
                HbtSide::Buy => ask <= entry_px,
                _ => bid >= entry_px,
            };
            spread = Some(ask - bid);
        }
    }
    // Диапазон номеров на клон — не пересекается ни со входом (лестница ≤ `MAX_ENTRY_LEGS` < `ID_STRIDE`),
    // ни с соседями: `(k+1) × ID_STRIDE` от базы входа этого круга.
    let variant_ids: Vec<u64> = (0..n as u64)
        .map(|k| entry_id_base.saturating_add(ID_STRIDE.saturating_mul(k.saturating_add(1))))
        .collect();
    *next_group_id = entry_id_base.saturating_add(ID_STRIDE.saturating_mul(n as u64 + 2));
    let results = run_round_group(
        bot,
        asset_no,
        &mut entry_state,
        entry_id,
        legs_of(variant_plans[0]),
        side,
        variant_plans,
        &variant_ids,
        data_end_ns.filter(|_| cfg.hold_skip),
    )?;
    let mut steps = Vec::with_capacity(n);
    for (i, r) in results.into_iter().enumerate() {
        variant_carries[i] = r.carry_out;
        if matches!(r.outcome, RoundOutcome::EndOfData) {
            steps.push(SignalStep::Submitted {
                crossed,
                spread,
                outcome: r.outcome,
                residual: None,
                idle_ns: r.idle_ns,
                exit_cancel_timeouts: r.exit_cancel_timeouts,
                orphan_fills: r.orphan_fills,
            });
            continue;
        }
        // Страховка остатка (2026-09-18), как у сольного `drive_signal` — по разу на вариант,
        // своим номером заявки (та же группа номеров, что и у выхода варианта — за пределами
        // диапазонов остальных вариантов и входа: `(n + 1) × ID_STRIDE` от базы круга плюс запас).
        let mut residual = None;
        let mut left = signed_residual(side, r.position);
        let mut flatten_id =
            entry_id_base.saturating_add(ID_STRIDE.saturating_mul(n as u64 + 2 + i as u64 * 64));
        // `idle_ns` — своя метка варианта (`r.idle_ns`, момент его собственного `Idle` в общем
        // движке группы), а не после общего цикла; страховка остатка, если была, её двигает
        // дальше — как у сольного `drive_signal`.
        let mut idle_ns = r.idle_ns;
        if left != 0.0 {
            let mut ended = false;
            while left != 0.0 {
                let id = flatten_id;
                flatten_id = flatten_id.saturating_add(1);
                match flatten_residual(bot, asset_no, id, left)? {
                    FlattenOutcome::Flat => break,
                    FlattenOutcome::Retry { left: rest } => left = rest,
                    FlattenOutcome::EndOfData => {
                        ended = true;
                        break;
                    }
                }
            }
            residual = Some(ended);
            idle_ns = bot.current_timestamp();
        }
        steps.push(SignalStep::Submitted {
            crossed,
            spread,
            outcome: r.outcome,
            residual,
            idle_ns,
            exit_cancel_timeouts: r.exit_cancel_timeouts,
            orphan_fills: r.orphan_fills,
        });
    }
    bot.clear_inactive_orders(Some(asset_no));
    Ok(steps)
}

/// Шаг драйвера на одном сигнале — всё, что требует движка: часы к `t0`,
/// проверка позиции, вход, круг, страховка остатка. Общий для сплошного
/// прогона (`drive_bounce`) и прогона по сетапам (`drive_bounce_windowed`):
/// различие только в том, откуда берётся движок, — иначе это была бы вторая
/// стратегия.
#[derive(Debug, Clone, PartialEq)]
enum SignalStep {
    /// Часы не дошли до `t0`: запись кончилась.
    EndOfData,
    /// Книга не была готова в момент касания — попытки не было.
    NotSubmitted { idle_ns: i64 },
    Submitted {
        crossed: bool,
        spread: Option<f64>,
        outcome: RoundOutcome,
        /// `Some(ended)` — остаток позиции закрывали по рынку; `ended` —
        /// запись кончилась в процессе.
        residual: Option<bool>,
        /// Часы стороны в момент, когда стратегия снова `Idle` (граница
        /// шага опроса после выхода или подтверждённой отмены): до него
        /// форма **занята** для любого сигнала.
        idle_ns: i64,
        /// F8b (В5/Р2): сколько раз круг пережил потолок ожидания
        /// подтверждения отмены лимитки выхода (`CANCEL_WAIT_NS`).
        exit_cancel_timeouts: u64,
        /// F8c (К1): исполнения заявок-сирот (после потолка) за круг.
        orphan_fills: u64,
    },
}

fn drive_signal<B, MD>(
    bot: &mut B,
    asset_no: usize,
    sig: &BounceSignal,
    cfg: &DriveConfig,
    next_id: &mut u64,
    carry: &mut OrphanCarry,
    data_end_ns: Option<i64>,
) -> Result<SignalStep, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let now = bot.current_timestamp();
    if sig.t0_ns > now {
        if bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
            return Ok(SignalStep::EndOfData);
        }
        // Сделки до постановки входа к пути исполнения не относятся (F3):
        // буфер чистится, детектор `run_round` видит только шаг исполнения.
        bot.clear_last_trades(Some(asset_no));
    }
    // Проверки «позиция уже открыта» по `Bot::position` здесь нет: с F4
    // (В-78) вход исполняется частично, а `Bot::position` крейта ведёт
    // позицию только по `Filled` (находка F3) — после первого же частичного
    // входа она врёт со знаком, и такая проверка объявила бы «занятыми» все
    // оставшиеся сигналы суток. Занятость ведёт `idle_ns` — момент, когда
    // стратегия вернулась в `Idle` со **своей** позицией, равной нулю
    // (`drive_bounce_with`), и в обоих драйверах он один и тот же (гейт
    // «те же круги» на `lob bounce-grid --driver full|setups`).

    let qty = sig.qty.unwrap_or(cfg.order_qty);
    debug_assert!(qty > 0.0, "размер круга обязан быть положительным: {qty}");
    let mut state = StrategyState::with_plan(asset_no, sig.sigma, qty, *next_id, sig.plan);
    *next_id = next_id.saturating_add(ID_STRIDE);
    // F8c: сироты прошлого круга живут дальше в новом состоянии — уборка
    // (повтор снятия, учёт исполнения) идёт с первого события этого круга.
    state.inherit_orphans(*carry);

    let (entry_id, side) = match on_event(bot, &mut state)? {
        Action::EntrySubmitted { order_id, side, .. } => (order_id, side),
        _ => {
            *carry = state.take_orphans();
            return Ok(SignalStep::NotSubmitted {
                idle_ns: bot.current_timestamp(),
            });
        }
    };
    // Замер механизма отказа (таск 38): в момент отправки входа смотрим,
    // пересекает ли его цена спред и каков спред. Пост-онли такую заявку
    // отвергает, обычный лимит — исполняет как тейкер.
    let mut crossed = false;
    let mut spread = None;
    if let Some(entry_px) = entry_market_px(sig.plan) {
        let d = bot.depth(asset_no);
        let (bid, ask) = (d.best_bid(), d.best_ask());
        if bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0 {
            crossed = match side {
                HbtSide::Buy => ask <= entry_px,
                _ => bid >= entry_px,
            };
            spread = Some(ask - bid);
        }
    }

    // Э-04б: пропуск пустых шагов — только с флагом и известным концом данных круга.
    let skip_cap = data_end_ns.filter(|_| cfg.hold_skip);
    let (outcome, residual_left) = run_round(
        bot,
        asset_no,
        &mut state,
        entry_id,
        legs_of(sig.plan),
        side,
        skip_cap,
    )?;
    if matches!(outcome, RoundOutcome::EndOfData) {
        *carry = state.take_orphans();
        return Ok(SignalStep::Submitted {
            crossed,
            spread,
            outcome,
            residual: None,
            idle_ns: bot.current_timestamp(),
            exit_cancel_timeouts: state.exit_cancel_timeouts(),
            orphan_fills: state.orphan_fills(),
        });
    }
    // Страховка (2026-09-18): круг кончился, а позиция осталась (например,
    // `Inconsistent`) — закрыть по рынку и посчитать, иначе все дальнейшие
    // сигналы молча «заняты» и остаток суток не считается вовсе. Размер
    // остатка — **своя** позиция стратегии (F4/В-78): `Bot::position` крейта
    // частичного исполнения не видит (находка F3), и по ней страховка
    // закрывала бы мнимый остаток, помечая честный круг `incomplete`.
    let mut residual = None;
    let mut left = signed_residual(side, residual_left);
    if left != 0.0 {
        let mut ended = false;
        while left != 0.0 {
            let id = *next_id;
            *next_id = next_id.saturating_add(1);
            match flatten_residual(bot, asset_no, id, left)? {
                FlattenOutcome::Flat => break,
                FlattenOutcome::Retry { left: rest } => left = rest,
                FlattenOutcome::EndOfData => {
                    ended = true;
                    break;
                }
            }
        }
        residual = Some(ended);
        if ended {
            *carry = state.take_orphans();
            return Ok(SignalStep::Submitted {
                crossed,
                spread,
                outcome,
                residual,
                idle_ns: bot.current_timestamp(),
                exit_cancel_timeouts: state.exit_cancel_timeouts(),
                orphan_fills: state.orphan_fills(),
            });
        }
    }
    bot.clear_inactive_orders(Some(asset_no));
    *carry = state.take_orphans();
    Ok(SignalStep::Submitted {
        crossed,
        spread,
        outcome,
        residual,
        idle_ns: bot.current_timestamp(),
        exit_cancel_timeouts: state.exit_cancel_timeouts(),
        orphan_fills: state.orphan_fills(),
    })
}

/// Память кругов сетки (G10, владелец 2026-09-23: «ускорить бэктест без потерь»). В прогоне по
/// сетапам круг — свежий движок над окном `t0`: его итог задают окно, сторона, план и настройка
/// драйвера, а не прежние круги формы и не набор (`--set` только выбирает сигналы). Поэтому круг
/// формы, уже посчитанный для одного набора, другой набор берёт готовым. Ключ — `t0`, сторона и
/// план целиком; вместе с шагом хранятся число израсходованных номеров заявок и сироты круга —
/// нумерация следующих кругов идёт так же, как без памяти (`OrphanCarry::rebased`). Только для
/// `drive_bounce_windowed_memo`: у сплошного прогона круг зависит от состояния движка.
#[derive(Default)]
pub struct RoundMemo {
    by_t0: std::collections::BTreeMap<i64, Vec<MemoEntry>>,
    hits: u64,
    misses: u64,
}

struct MemoEntry {
    sigma: i8,
    plan: TradePlan,
    /// R2: размер круга — часть ключа, как план: тот же сигнал с другим лотом
    /// — другой круг (очередь, частичное исполнение).
    qty: Option<f64>,
    /// Сироты, с которыми круг **начинался** (номера — от базы прогона, `rebased(id_base, 0)`): часть ключа.
    /// Круг наследует сирот прошлого сигнала (`inherit_orphans`), а прошлый сигнал у наборов с разным
    /// допуском разный — круг с другими сиротами на входе из памяти брать нельзя (ревью бага memo 27.09).
    carry_in: OrphanCarry,
    step: Option<SignalStep>,
    /// База номеров заявок прогона, посчитавшего круг, и сколько номеров круг израсходовал;
    /// `0` — движок сигнала не запускался (сироты входа не трогались).
    id_base: u64,
    ids_used: u64,
    carry_out: OrphanCarry,
}

impl RoundMemo {
    /// Круг из памяти для прогона с базой номеров `id_base`: шаг, число номеров и сироты
    /// (в нумерации этого прогона; `None` — движок не запускался, сироты прежние).
    fn recall(
        &mut self,
        sig: &BounceSignal,
        id_base: u64,
        carry_in: OrphanCarry,
    ) -> Option<(Option<SignalStep>, u64, Option<OrphanCarry>)> {
        let carry_key = carry_in.rebased(id_base, 0);
        let hit = self
            .by_t0
            .get(&sig.t0_ns)
            .and_then(|v| {
                v.iter().find(|e| {
                    e.sigma == sig.sigma
                        && e.plan == sig.plan
                        && e.qty == sig.qty
                        && e.carry_in == carry_key
                })
            })
            .map(|e| {
                let carry = (e.ids_used > 0).then(|| e.carry_out.rebased(e.id_base, id_base));
                (e.step.clone(), e.ids_used, carry)
            });
        if hit.is_some() {
            self.hits += 1;
        } else {
            self.misses += 1;
        }
        hit
    }

    // Поля одной записи памяти — по смыслу, структура ради счётчика аргументов — лишний слой.
    #[allow(clippy::too_many_arguments)]
    fn store(
        &mut self,
        sig: &BounceSignal,
        step: Option<SignalStep>,
        id_base: u64,
        ids_used: u64,
        carry_in: OrphanCarry,
        carry_out: OrphanCarry,
    ) {
        self.by_t0.entry(sig.t0_ns).or_default().push(MemoEntry {
            sigma: sig.sigma,
            plan: sig.plan,
            qty: sig.qty,
            carry_in: carry_in.rebased(id_base, 0),
            step,
            id_base,
            ids_used,
            carry_out,
        });
    }

    /// Есть ли в памяти круг для сигнала с этими сиротами на входе (Э-08: групповой предсчёт не считает
    /// уже известное); счётчики попаданий не трогает. Сироты — в нумерации от базы 0.
    fn contains(&self, sig: &BounceSignal, carry_key: OrphanCarry) -> bool {
        self.by_t0.get(&sig.t0_ns).is_some_and(|v| {
            v.iter().any(|e| {
                e.sigma == sig.sigma
                    && e.plan == sig.plan
                    && e.qty == sig.qty
                    && e.carry_in == carry_key
            })
        })
    }

    /// Сколько кругов взято из памяти и сколько посчитано движком.
    pub fn stats(&self) -> (u64, u64) {
        (self.hits, self.misses)
    }
}

impl std::fmt::Debug for RoundMemo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RoundMemo")
            .field("t0", &self.by_t0.len())
            .field("hits", &self.hits)
            .field("misses", &self.misses)
            .finish()
    }
}

impl BounceRun {
    /// Прогон, который не сделал ни шага: запись кончилась до первого сигнала.
    fn nothing(profile: u16, signals: u64) -> Self {
        BounceRun {
            profile,
            signals,
            fills: Vec::new(),
            fill_signal: Vec::new(),
            fill_reason: Vec::new(),
            fill_exit_ns: Vec::new(),
            exits: ExitTally::default(),
            entry_rejected: 0,
            rejected_postonly: 0,
            entry_crossed: 0,
            entry_cancelled_ttl: 0,
            entry_cancelled_wall_dead: 0,
            entry_cancelled_price_left: 0,
            entry_cancelled_cancel_timeout: 0,
            exit_cancel_timeout: 0,
            orphan_fills: 0,
            spread_at_entry: Vec::new(),
            submitted_signal: Vec::new(),
            busy_signal: Vec::new(),
            busy_wait_ns_max: 0,
            round_ns_max: 0,
            misses: MissLedger::default(),
            observations: Vec::new(),
            incomplete: true,
            residual_flattened: 0,
            trace: Vec::new(),
        }
    }
}

/// Цикл по сигналам, общий для обоих драйверов. `source(sig, step)` даёт шагу
/// движок и возвращает его результат; `None` — для сигнала нет данных (там,
/// где сплошной прогон упёрся бы в конец записи). Учёт кругов, промахов и
/// причин выхода — здесь, движка он не касается.
/// Круг упёрся в конец данных (Р6): `SignalStep::EndOfData`, исход круга `EndOfData` или остаток, который
/// не удалось закрыть до конца данных (`residual: Some(true)`) — все пути, которыми `ElapseResult::EndOfData`
/// выходит из `drive_signal`.
fn step_hit_end_of_data(s: &SignalStep) -> bool {
    match s {
        SignalStep::EndOfData => true,
        SignalStep::Submitted {
            outcome, residual, ..
        } => matches!(outcome, RoundOutcome::EndOfData) || *residual == Some(true),
        _ => false,
    }
}

/// Пересчётов круга из-за короткого горизонта развёртки (Р6) — счётчик процесса; `bounce-grid` печатает
/// прирост по символу-суткам. Пересчёт не меняет результат, только время.
pub static HORIZON_RETRIES: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Прогонов движка и развёрнутых строк по номеру попытки (0, 1, 2, 3+) — замер TK-049, на итог не влияет.
pub static ATTEMPT_RUNS: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];
pub static ATTEMPT_ROWS: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

/// Шаги `run_round` по виду (0 — пошаговый без заявок, 1 — пошаговый с открытыми заявками, 2 — прыжок удержания) — замер TK-049.
pub static STEP_KINDS: [std::sync::atomic::AtomicU64; 3] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 3];

/// Пропуск `on_event` в удержании, когда входы решения не менялись (`ALPHA_SKIP_SAME=1`, TK-050): решение на
/// тех же входах повторяется; умолчание — зовём на каждом шаге.
fn skip_same() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_SKIP_SAME").is_some_and(|v| v == "1"))
}

/// Пропущено вызовов `on_event` (процесс; на итог счёта не влияет).
pub static SIG_SKIPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Подпись входов последнего вызова круга и число её повторов: пропуск — со второго повтора (первый повтор
/// доводит накопители состояния до неподвижной точки).
#[derive(Clone, Copy, Default)]
struct SigMemo {
    sig: Option<[u64; 5]>,
    reps: u8,
}

impl SigMemo {
    /// Можно ли пропустить вызов на этих входах.
    fn skip(&mut self, now: Option<[u64; 5]>) -> bool {
        match now {
            Some(s) if self.sig == Some(s) => {
                let skip = self.reps >= 1;
                self.reps = self.reps.saturating_add(1);
                if skip {
                    SIG_SKIPS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                }
                skip
            }
            other => {
                self.sig = other;
                self.reps = 0;
                false
            }
        }
    }

    fn reset(&mut self) {
        self.sig = None;
    }
}

fn note_attempt(attempt: u32, rows: usize) {
    let k = (attempt as usize).min(3);
    ATTEMPT_RUNS[k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    ATTEMPT_ROWS[k].fetch_add(rows as u64, std::sync::atomic::Ordering::Relaxed);
}

/// Запас горизонта развёртки круга сверх `entry_ttl + deadline` плана (Р6): подтверждения и отмены после
/// дедлайна. Точность не зависит от него — короткий горизонт ловит проверка и пересчитывает круг.
const HORIZON_MARGIN_NS: i64 = 60_000_000_000;

/// Горизонт первой попытки развёртки (Р6-поправка, 26.09): большинство кругов (вход не исполнен, быстрый
/// выход) кончается за минуты, а полный горизонт плана — часы (`entry_ttl` + дедлайн); развёртка на весь
/// горизонт на каждый сигнал делала касания ×5 медленнее старого пути (CRV 22.09, 8 форм: 12 → 71 с).
/// Первая попытка — не дальше этого, вторая — сразу полный горизонт, дальше удвоение: лишняя работа
/// длинного круга ограничена первой попыткой. Точность от числа не зависит (проверка конца данных), только
/// время; значение — замер `prof/r6fix.log` (findings T-23).
const FIRST_HORIZON_NS: i64 = 300_000_000_000;

// Тестовая подмена начального горизонта развёртки (условие Судьи 2 к Р6): искусственно малый горизонт
// заставляет пересчитывать почти каждый круг — итог обязан остаться побайтно тем же.
#[cfg(test)]
thread_local! {
    pub(crate) static HORIZON_SPAN_OVERRIDE_NS: std::cell::Cell<Option<i64>> =
        const { std::cell::Cell::new(None) };
}

fn horizon_span_ns(plan: &TradePlan) -> i64 {
    #[cfg(test)]
    if let Some(v) = HORIZON_SPAN_OVERRIDE_NS.with(std::cell::Cell::get) {
        return v.max(1);
    }
    plan.round_span_ns()
        .saturating_add(HORIZON_MARGIN_NS)
        .max(1)
}

fn drive_bounce_with<B, MD, S>(
    asset_no: usize,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    mut memo: Option<&mut RoundMemo>,
    mut source: S,
) -> Result<BounceRun, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
    // Источник круга: сигнал, номер попытки (Р6 — пересчёт с удвоенным горизонтом развёртки) и шаг
    // стратегии; отдаёт шаг и «данных хватило» (`false` — круг пересчитывается с восстановленными
    // номерами заявок и переносом сирот, как будто первой попытки не было).
    S: FnMut(
        &BounceSignal,
        u32,
        // Шаг стратегии получает конец данных круга (Э-04б: `None` — неизвестен, пропуска шагов нет).
        &mut dyn FnMut(&mut B, Option<i64>) -> Result<SignalStep, B::Error>,
    ) -> Result<Option<(SignalStep, bool)>, B::Error>,
{
    let mut order: Vec<BounceSignal> = signals.to_vec();
    order.sort_by_key(|s| s.t0_ns);
    let profile = signals.first().map(|s| s.profile).unwrap_or(0);
    let mut fills: Vec<Fill> = Vec::new();
    let mut misses = MissLedger::default();
    let mut observations: Vec<FillObservation> = Vec::new();
    let mut exits = ExitTally::default();
    let mut entry_rejected: u64 = 0;
    let mut rejected_postonly: u64 = 0;
    let mut entry_crossed: u64 = 0;
    // Снятия неисполненного входа по причинам (F5, В-74) — в `forms.csv`.
    let mut entry_cancelled_ttl: u64 = 0;
    let mut entry_cancelled_wall_dead: u64 = 0;
    let mut entry_cancelled_price_left: u64 = 0;
    // F8b (В5/Р2): вход, снятый потолком ожидания подтверждения отмены, идёт
    // той же строкой, что и прочие снятия, — причиной `CancelTimeout`.
    let mut entry_cancelled_cancel_timeout: u64 = 0;
    let mut exit_cancel_timeout: u64 = 0;
    let mut orphan_fills: u64 = 0;
    let mut spread_at_entry: Vec<f64> = Vec::new();
    let mut busy_signal: Vec<usize> = Vec::new();
    let mut submitted_signal: Vec<usize> = Vec::new();
    let mut busy_wait_ns_max: i64 = 0;
    let mut round_ns_max: i64 = 0;
    let mut fill_signal: Vec<usize> = Vec::new();
    let mut fill_reason: Vec<ExitReason> = Vec::new();
    let mut fill_exit_ns: Vec<i64> = Vec::new();
    let mut next_id = cfg.first_order_id;
    // F8c: сироты переносятся между кругами суток (см. `drive_signal`).
    let mut carry = OrphanCarry::NONE;
    let mut incomplete = false;
    let mut residual_flattened: u64 = 0;
    // Форма занята, пока стратегия не вернулась в `Idle` после предыдущего
    // сигнала (выход подтверждён или отмена входа подтверждена). Раньше
    // занятость считалась только до `exit_ts` исполненного круга, а сигнал в
    // хвосте круга или во время таймаута входа стартовал **с опозданием** —
    // в момент освобождения, по устаревшему касанию (артефакт драйвера,
    // 2026-09-18); теперь такой сигнал — промах «занято» в обоих драйверах.
    let mut idle_ns: i64 = i64::MIN;

    let miss_observation = |t0_ns: i64| FillObservation {
        day_cluster: day_index_ns(t0_ns),
        net_bps: 0.0,
        filled: false,
    };

    let mut trace: Vec<SignalTrace> = Vec::new();
    for (sig_idx, sig) in order.iter().enumerate() {
        if entry_side(sig.sigma).is_none() {
            continue;
        }
        if cfg.busy_skip && sig.t0_ns < idle_ns {
            busy_signal.push(sig_idx);
            busy_wait_ns_max = busy_wait_ns_max.max(idle_ns.saturating_sub(sig.t0_ns));
            misses.record(MissReason::PositionBusy);
            observations.push(miss_observation(sig.t0_ns));
            continue;
        }
        // Память кругов (G10): круг, уже посчитанный над тем же окном с тем же планом, берётся
        // готовым; номера заявок и сироты продолжаются так, как если бы его считали здесь.
        let recalled = memo
            .as_deref_mut()
            .and_then(|m| m.recall(sig, next_id, carry));
        let step = match recalled {
            Some((step, ids_used, carry_out)) => {
                next_id = next_id.saturating_add(ids_used);
                if let Some(c) = carry_out {
                    carry = c;
                }
                step
            }
            None => {
                let id_base = next_id;
                let carry_base = carry;
                let mut attempt = 0u32;
                let step = loop {
                    let r = source(sig, attempt, &mut |bot: &mut B, data_end: Option<i64>| {
                        drive_signal(bot, asset_no, sig, cfg, &mut next_id, &mut carry, data_end)
                    })?;
                    match r {
                        Some((_, false)) => {
                            next_id = id_base;
                            carry = carry_base;
                            attempt = attempt.saturating_add(1);
                            HORIZON_RETRIES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        }
                        Some((step, true)) => break Some(step),
                        None => break None,
                    }
                };
                if let Some(m) = memo.as_deref_mut() {
                    m.store(
                        sig,
                        step.clone(),
                        id_base,
                        next_id.wrapping_sub(id_base),
                        carry_base,
                        carry,
                    );
                }
                step
            }
        };
        // Без пропуска «занято» (T-31) обрыв суток счёт не прерывает: сигналы после него — свои круги,
        // а обрывает ли шаг сутки, решает фильтр по следу (`SignalTrace`) — если этот шаг он примет.
        let end_day = |trace: &mut Vec<SignalTrace>, step: TraceStep, residual: Option<bool>| {
            if !cfg.busy_skip {
                trace.push(SignalTrace {
                    signal: sig_idx,
                    step,
                    idle_ns: i64::MIN,
                    residual,
                });
            }
            cfg.busy_skip
        };
        let Some(step) = step else {
            incomplete = true;
            if end_day(&mut trace, TraceStep::NoWindow, None) {
                break;
            }
            continue;
        };
        match step {
            SignalStep::EndOfData => {
                incomplete = true;
                if end_day(&mut trace, TraceStep::EndOfData, None) {
                    break;
                }
            }
            SignalStep::NotSubmitted { idle_ns: idle } => {
                idle_ns = idle;
                misses.record(MissReason::EntryTimeout);
                observations.push(miss_observation(sig.t0_ns));
                if !cfg.busy_skip {
                    trace.push(SignalTrace {
                        signal: sig_idx,
                        step: TraceStep::NotSubmitted,
                        idle_ns: idle,
                        residual: None,
                    });
                }
            }
            SignalStep::Submitted {
                crossed,
                spread,
                outcome,
                residual,
                idle_ns: idle,
                exit_cancel_timeouts,
                orphan_fills: orphans,
            } => {
                idle_ns = idle;
                exit_cancel_timeout = exit_cancel_timeout.saturating_add(exit_cancel_timeouts);
                orphan_fills = orphan_fills.saturating_add(orphans);
                submitted_signal.push(sig_idx);
                if crossed {
                    entry_crossed = entry_crossed.saturating_add(1);
                }
                if let Some(s) = spread {
                    spread_at_entry.push(s);
                }
                let mut kind = TraceStep::Filled;
                match outcome {
                    RoundOutcome::EndOfData => {
                        incomplete = true;
                        if end_day(&mut trace, TraceStep::EndOfData, None) {
                            break;
                        }
                        continue;
                    }
                    RoundOutcome::Inconsistent => {
                        incomplete = true;
                        kind = TraceStep::Inconsistent;
                    }
                    RoundOutcome::TimedOut {
                        entry_status,
                        legs_rejected,
                        reason,
                    } => {
                        if matches!(entry_status, Some(Status::Rejected)) {
                            entry_rejected = entry_rejected.saturating_add(1);
                        }
                        // F5 (В-74): снятие по событию рынка и по потолку —
                        // разные строки `forms.csv`; «сигнал без входа»
                        // (`NotPlaced`) уже посчитан `rejected_postonly`.
                        match reason {
                            EntryCancelReason::Ttl => {
                                entry_cancelled_ttl = entry_cancelled_ttl.saturating_add(1);
                            }
                            EntryCancelReason::WallDead => {
                                entry_cancelled_wall_dead =
                                    entry_cancelled_wall_dead.saturating_add(1);
                            }
                            EntryCancelReason::PriceLeft => {
                                entry_cancelled_price_left =
                                    entry_cancelled_price_left.saturating_add(1);
                            }
                            EntryCancelReason::NotPlaced => {}
                            EntryCancelReason::CancelTimeout => {
                                entry_cancelled_cancel_timeout =
                                    entry_cancelled_cancel_timeout.saturating_add(1);
                            }
                        }
                        // В-72: нога, не поставленная биржей (пост-онли
                        // `Expired` или `Rejected`), — отказ, а не заказ;
                        // круг без единой поставленной ноги идёт сюда же и
                        // считается «сигналом без входа» (`EntryTimeout`), а
                        // не «занято».
                        rejected_postonly =
                            rejected_postonly.saturating_add(u64::from(legs_rejected));
                        kind = TraceStep::TimedOut;
                        misses.record(MissReason::EntryTimeout);
                        observations.push(miss_observation(sig.t0_ns));
                    }
                    RoundOutcome::Filled {
                        fill,
                        exit_ts,
                        reason,
                        partial,
                    } => {
                        match reason {
                            ExitReason::Take => exits.take += 1,
                            ExitReason::Stop => exits.stop += 1,
                            ExitReason::Deadline => exits.deadline += 1,
                            ExitReason::Horizon => exits.horizon += 1,
                            ExitReason::Trail => exits.trail += 1,
                            ExitReason::Early => exits.early += 1,
                            ExitReason::Eaten => exits.eaten += 1,
                            ExitReason::EatenByTrades => exits.eaten_by_trades += 1,
                            ExitReason::WallGone => exits.wall_gone += 1,
                            // TK-014: обе причины `weat*` — в `n_eaten_by_trades` (колонки те же).
                            ExitReason::WallEatBtc | ExitReason::WallEatLocal => {
                                exits.eaten_by_trades += 1
                            }
                        }
                        if partial {
                            exits.partial += 1;
                        }
                        rejected_postonly =
                            rejected_postonly.saturating_add(u64::from(fill.legs_rejected));
                        let net = roundtrip_net_bps(&fill);
                        observations.push(FillObservation {
                            day_cluster: day_index_ns(sig.t0_ns),
                            net_bps: net.unwrap_or(0.0),
                            filled: net.is_some(),
                        });
                        fills.push(fill);
                        fill_signal.push(sig_idx);
                        fill_reason.push(reason);
                        fill_exit_ns.push(exit_ts);
                        round_ns_max = round_ns_max.max(exit_ts.saturating_sub(sig.t0_ns));
                    }
                }
                if let Some(ended) = residual {
                    residual_flattened = residual_flattened.saturating_add(1);
                    incomplete = true;
                    if ended {
                        if end_day(&mut trace, kind, residual) {
                            break;
                        }
                        continue;
                    }
                }
                if !cfg.busy_skip {
                    trace.push(SignalTrace {
                        signal: sig_idx,
                        step: kind,
                        idle_ns: idle,
                        residual,
                    });
                }
            }
        }
    }

    Ok(BounceRun {
        profile,
        signals: order.len() as u64,
        fills,
        fill_signal,
        fill_reason,
        fill_exit_ns,
        exits,
        entry_rejected,
        rejected_postonly,
        entry_crossed,
        entry_cancelled_ttl,
        entry_cancelled_wall_dead,
        entry_cancelled_price_left,
        entry_cancelled_cancel_timeout,
        exit_cancel_timeout,
        orphan_fills,
        spread_at_entry,
        submitted_signal,
        busy_signal,
        busy_wait_ns_max,
        round_ns_max,
        misses,
        observations,
        incomplete,
        residual_flattened,
        trace,
    })
}

/// Прогон сделки-отскока по сигналам **одного** профиля касаний: план у
/// каждого сигнала свой (цены считает уровень, не движок). Возвращает круги,
/// промахи по причинам и число выходов по каждой причине — из этого CLI
/// собирает `n_filled`/`fill`/`net_fill` и `n_stop`/`n_take`/`n_timeout`.
///
/// Это **сплошной** прогон: один движок на всю запись, между сигналами он
/// применяет к книге все события. Для сетки форм есть `drive_bounce_windowed`
/// — тот же цикл, движок только внутри кругов; сплошной остаётся эталоном
/// (гейт «побайтово те же круги», `lob bounce-grid --driver full`).
pub fn drive_bounce<B, MD>(
    bot: &mut B,
    asset_no: usize,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
) -> Result<BounceRun, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    if bot.elapse(0)? == ElapseResult::EndOfData {
        let profile = signals.first().map(|s| s.profile).unwrap_or(0);
        return Ok(BounceRun::nothing(profile, signals.len() as u64));
    }
    let run = drive_bounce_with::<B, MD, _>(asset_no, signals, cfg, None, |_, _, step| {
        step(bot, None).map(|s| Some((s, true)))
    })?;
    bot.clear_inactive_orders(Some(asset_no));
    Ok(run)
}

/// Прогон **по сетапам** (решение владельца 2026-09-18: «биржа только в
/// сетапах, ничего вхолостую»): движок живёт от касания до конца круга. На
/// сигнал строится `Backtest` над снимком книги в `t0` (`SignalWindows`,
/// одна книга на все формы) и событиями дальше — без копии; между сигналами
/// биржа не работает вовсе, и цена суток определяется числом касаний и
/// длиной кругов, а не числом событий в стакане.
///
/// Точность: снимок — книга движка (`FastMarketDepth` — копия `HashMapMarketDepth` крейта, Э-05) после тех же строк, по всем полям
/// (строит её своя книга окон `WindowDepth`, К3)
/// (обе метки `<= t0`, см. `SignalWindows`), со всеми полями лучших/крайних
/// тиков, один на обе стороны движка; строки с одной меткой за `t0` каждая
/// сторона доберёт из среза сама по разу, как и в сплошном прогоне; часы
/// окна прибиты к `t0` строкой-якорем. Гейт — побайтово те же круги, что у
/// `drive_bounce` (тест и `--driver full`).
pub fn drive_bounce_windowed<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
) -> Result<BounceRun, BacktestError> {
    windowed_with(events, windows, signals, cfg, exec_latency, None)
}

/// То же, что `drive_bounce_windowed`, но с памятью кругов (G10): круги, уже посчитанные над теми
/// же окнами с теми же планами (другим набором той же формы), берутся из `memo`. Итог — побайтово
/// тот же, что без памяти (гейт «те же байты» в `bounce-grid --round-memo on|off`).
pub fn drive_bounce_windowed_memo<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    memo: &mut RoundMemo,
) -> Result<BounceRun, BacktestError> {
    windowed_with(events, windows, signals, cfg, exec_latency, Some(memo))
}

/// Круги по окнам. Строки `[Event]` крейт занимает как есть (прежний путь). Компактные строки (Р6)
/// разворачиваются на круг в буфер потока — от `w.start` до горизонта: первая попытка — не дальше
/// `FIRST_HORIZON_NS`, затем `t0 + (entry_ttl + deadline + запас) × 2^(попытка − 1)`; после круга
/// проверка: крейт не дошёл до конца развёрнутого (у последней строки и
/// `local_ts`, и `exch_ts` позже часов конца круга — каждая сторона крейта берёт строку, только когда её
/// часы дошли) либо развёрнут весь хвост суток. Иначе круг пересчитывается с удвоенным горизонтом
/// (`drive_bounce_with` восстанавливает номера заявок и сирот) — поэтому круги побайтно те же, что над
/// всем хвостом (условие Судьи к Р6).
/// Входная часть плана (Э-08): поля, которые читает фаза входа, — план с обнулёнными полями выхода
/// (стоп, тейк, дедлайн, трейл, досрочный выход, E7/F7). Формы с равной входной частью на сигнале
/// разворачивают один общий вход.
fn entry_part(plan: TradePlan) -> TradePlan {
    let mut p = plan;
    if let TradePlan::Bounce {
        stop_px,
        take_px,
        deadline_ns,
        trail_bps,
        trail_activate_bps,
        early_exit_ns,
        take_frac,
        eaten_half_pct,
        eaten_all_pct,
        eaten_half_frac,
        exit_eat_pct,
        exit_gone_pct,
        gone_trail_bps,
        gone_stop,
        wall_eat,
        ..
    } = &mut p
    {
        *stop_px = 0.0;
        *take_px = 0.0;
        *deadline_ns = 0;
        *trail_bps = 0.0;
        *trail_activate_bps = 0.0;
        *early_exit_ns = 0;
        *take_frac = 0.0;
        *eaten_half_pct = 0.0;
        *eaten_all_pct = 0.0;
        *eaten_half_frac = 0.0;
        *exit_eat_pct = 0.0;
        *exit_gone_pct = 0.0;
        *gone_trail_bps = 0.0;
        *gone_stop = crate::lob::strategy::GoneStop::Off;
        *wall_eat = crate::lob::strategy::WallEatExit::OFF;
    }
    p
}

/// Групповой круг одного сигнала в свежем движке окна (Э-08) — те же попытки горизонта развёртки, что у
/// `windowed_with` (Р6). `None` — окна или данных после `t0` нет. Вариант с сиротами на выходе
/// возвращается как `EndOfData` — в память он не попадёт (драйвер посчитает его сам).
fn group_round_in_window<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    rep: &BounceSignal,
    plans: &[TradePlan],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    buf: &mut Vec<Event>,
) -> Result<Option<Vec<SignalStep>>, BacktestError> {
    let Some(w) = windows.window_at(rep.t0_ns) else {
        return Ok(None);
    };
    let start = events.skip_to(w.start);
    if start >= events.len() {
        return Ok(None);
    }
    let span_full = plans.iter().map(horizon_span_ns).max().unwrap_or(1);
    let mut attempt = 0u32;
    loop {
        let (rest, whole_tail): (&[Event], bool) = match events.as_events() {
            Some(all) => (&all[w.start..], true),
            None => {
                let span = match attempt {
                    0 => span_full.min(FIRST_HORIZON_NS),
                    a => span_full
                        .checked_shl(a - 1)
                        .filter(|v| *v > 0)
                        .unwrap_or(i64::MAX),
                };
                let until = rep.t0_ns.saturating_add(span);
                buf.clear();
                let mut i = start;
                while i < events.len() && events.row_local_ts(i) <= until {
                    buf.push(events.row(i));
                    i += 1;
                }
                (&buf[..], i >= events.len())
            }
        };
        note_attempt(attempt, rest.len());
        let last = rest.last().map(|e| (e.local_ts, e.exch_ts));
        let data_end = last.map(|(local, exch)| local.min(exch));
        let (steps, now) = with_backtest_over_window(
            &w.depth,
            rep.t0_ns,
            rest,
            windows.tick_size,
            windows.lot_size,
            exec_latency,
            cfg.queue_model,
            |bt| -> Result<(Option<Vec<SignalStep>>, i64), BacktestError> {
                if bt.elapse(0)? == ElapseResult::EndOfData {
                    return Ok((None, bt.current_timestamp()));
                }
                let mut gid = GROUP_ID_BASE;
                let mut carries = vec![OrphanCarry::NONE; plans.len()];
                let steps =
                    drive_signal_group(bt, 0, rep, plans, cfg, &mut gid, &mut carries, data_end)?;
                let steps = steps
                    .into_iter()
                    .zip(carries)
                    .map(|(s, c)| {
                        if c.is_empty() {
                            s
                        } else {
                            SignalStep::EndOfData
                        }
                    })
                    .collect();
                Ok((Some(steps), bt.current_timestamp()))
            },
        )?;
        let Some(steps) = steps else {
            return Ok(None);
        };
        let enough = whole_tail
            || (!steps.iter().any(step_hit_end_of_data)
                && last.is_some_and(|(local, exch)| local > now && exch > now));
        if enough {
            return Ok(Some(steps));
        }
        attempt = attempt.saturating_add(1);
        HORIZON_RETRIES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
}

/// Групповых кругов Э-08 — счётчик процесса для тестов; на итог не влияет.
pub static EXIT_GROUP_ROUNDS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Э-08 («один проход на вход», план принят Судьёй, reviews/e08-exit-group-plan-2026-09-27.md): круги
/// группы форм считаются заранее одним движком на сигнал и кладутся в память кругов каждой формы — потом
/// обычный драйвер (`drive_bounce_windowed_memo`) берёт их оттуда. Группа на сигнале — формы с равными
/// `sigma`, размером и входной частью плана (`entry_part`); вход общий, выходы — свои (`drive_signal_group`).
/// В память идёт только чистый круг варианта: без сирот на выходе, без страховки остатка и не упёршийся в
/// конец данных — остальное драйвер посчитает сам, как раньше. Ключ памяти — с пустыми сиротами на входе:
/// у формы с сиротами прошлого сигнала круг из памяти не возьмётся. Только без пропуска «занято»
/// (`busy_skip = false`: у каждого сигнала свой свежий движок). Возвращает число групповых кругов.
pub fn precompute_exit_group<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    forms_signals: &[&[BounceSignal]],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    memos: &mut [&mut RoundMemo],
) -> Result<u64, BacktestError> {
    debug_assert_eq!(forms_signals.len(), memos.len());
    if cfg.busy_skip {
        return Ok(0);
    }
    let mut by_t0: std::collections::BTreeMap<i64, Vec<(usize, BounceSignal)>> =
        std::collections::BTreeMap::new();
    for (k, sigs) in forms_signals.iter().enumerate() {
        for sig in sigs.iter() {
            if entry_side(sig.sigma).is_some() {
                by_t0.entry(sig.t0_ns).or_default().push((k, *sig));
            }
        }
    }
    let mut groups: u64 = 0;
    let mut buf: Vec<Event> = Vec::new();
    for members in by_t0.values() {
        let mut parts: Vec<Vec<(usize, BounceSignal)>> = Vec::new();
        for &(k, sig) in members {
            if memos[k].contains(&sig, OrphanCarry::NONE) {
                continue;
            }
            let key = (sig.sigma, sig.qty, entry_part(sig.plan));
            match parts
                .iter_mut()
                .find(|g| (g[0].1.sigma, g[0].1.qty, entry_part(g[0].1.plan)) == key)
            {
                Some(g) => g.push((k, sig)),
                None => parts.push(vec![(k, sig)]),
            }
        }
        for part in parts.into_iter().filter(|g| g.len() > 1) {
            let rep = part[0].1;
            let plans: Vec<TradePlan> = part.iter().map(|(_, s)| s.plan).collect();
            let Some(steps) =
                group_round_in_window(events, windows, &rep, &plans, cfg, exec_latency, &mut buf)?
            else {
                continue;
            };
            groups += 1;
            EXIT_GROUP_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            for ((k, sig), step) in part.iter().zip(steps) {
                let clean = match &step {
                    SignalStep::EndOfData => false,
                    SignalStep::NotSubmitted { .. } => true,
                    SignalStep::Submitted {
                        outcome, residual, ..
                    } => residual.is_none() && !matches!(outcome, RoundOutcome::EndOfData),
                };
                if clean {
                    memos[*k].store(
                        sig,
                        Some(step),
                        0,
                        ID_STRIDE,
                        OrphanCarry::NONE,
                        OrphanCarry::NONE,
                    );
                }
            }
        }
    }
    Ok(groups)
}

fn windowed_with<R: EventRows + ?Sized>(
    events: &R,
    windows: &SignalWindows,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
    memo: Option<&mut RoundMemo>,
) -> Result<BounceRun, BacktestError> {
    let mut buf: Vec<Event> = Vec::new();
    drive_bounce_with::<Backtest<FastMarketDepth>, FastMarketDepth, _>(
        0,
        signals,
        cfg,
        memo,
        |sig, attempt, step| {
            let Some(w) = windows.window_at(sig.t0_ns) else {
                return Ok(None);
            };
            let start = events.skip_to(w.start);
            if start >= events.len() {
                // Строк после `t0` нет — сплошной прогон здесь упёрся бы в
                // конец записи, не дойдя до сигнала.
                return Ok(None);
            }
            let (rest, whole_tail): (&[Event], bool) = match events.as_events() {
                Some(all) => (&all[w.start..], true),
                None => {
                    let span = horizon_span_ns(&sig.plan);
                    let span = match attempt {
                        0 => span.min(FIRST_HORIZON_NS),
                        a => span
                            .checked_shl(a - 1)
                            .filter(|v| *v > 0)
                            .unwrap_or(i64::MAX),
                    };
                    let until = sig.t0_ns.saturating_add(span);
                    buf.clear();
                    let mut i = start;
                    while i < events.len() && events.row_local_ts(i) <= until {
                        buf.push(events.row(i));
                        i += 1;
                    }
                    (&buf[..], i >= events.len())
                }
            };
            note_attempt(attempt, rest.len());
            let last = rest.last().map(|e| (e.local_ts, e.exch_ts));
            // Э-04б: конец данных круга — меньшая из меток последней строки (до неё крейт не исчерпан).
            let data_end = last.map(|(local, exch)| local.min(exch));
            with_backtest_over_window(
                &w.depth,
                sig.t0_ns,
                rest,
                windows.tick_size,
                windows.lot_size,
                exec_latency,
                cfg.queue_model,
                |bt| {
                    let s = if bt.elapse(0)? == ElapseResult::EndOfData {
                        SignalStep::EndOfData
                    } else {
                        step(bt, data_end)?
                    };
                    Ok((s, bt.current_timestamp()))
                },
            )
            .map(|(s, now)| {
                // Крейт упёрся в конец развёрнутого — в любом из трёх видов, которыми конец данных
                // выходит из круга; иначе (и последняя строка позже часов) он видел ровно то же, что
                // увидел бы над всем хвостом.
                let enough = whole_tail
                    || (!step_hit_end_of_data(&s)
                        && last.is_some_and(|(local, exch)| local > now && exch > now));
                Some((s, enough))
            })
        },
    )
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
// Строитель `Backtest` крейта: движок целиком в одном месте.
// ---------------------------------------------------------------------------

/// Строит `Backtest` крейта: модель очереди и модель исполнения — по
/// `queue_model` (F3 плана 2026-09-20), комиссии
/// `costs::{MAKER_FEE_BPS, TAKER_FEE_BPS}`, задержка из замеренной RTT
/// (`latency_from_rtt`). `events` — уже переведённый в формат крейта поток;
/// перевод из `Feed`/площадки — забота вызывающего (`commands::lob::backtest`),
/// не этого файла (грепом-тест внизу).
///
/// # Три пути исполнения крейта (знать наизусть — они определяют, что
/// оптимистично, а что нет)
///
/// 1. **Сделки на нашей цене** — исполнение частичное, через модель очереди:
///    сделка двигает позицию в очереди (`QueueModel::trade`), снятия впереди —
///    `depth` с вероятностью `P` (`ProbQueueModel`); исполняется тот объём,
///    на который очередь ушла в минус, кратно шагу лота
///    (`PartialFillExchange::check_if_buy_filled`, `Ordering::Equal`). Честно,
///    с вероятностной поправкой на снятия впереди. У `RiskAdverse` тот же
///    путь, но позиция двигается только сделками, а исполняется заявка
///    целиком.
/// 2. **Сделка ниже нашей цены (для покупки; в стену)** — исполняет **весь
///    остаток** заявки: приоритет цены (`Ordering::Greater` → `fill(leaves_qty)`).
///    Верно для стоящей заявки.
/// 3. **Лучший аск опустился до нашей цены без сделки** — `on_best_ask_update`
///    исполняет **весь остаток** (продавец пересёк нас лимиткой; для продажи —
///    зеркально, `on_best_bid_update`). Верно по смыслу, но **оптимистично по
///    размеру**: крейт отдаёт весь остаток, а не объём лучшего уровня.
///    Счётчик кругов по этому пути — `Fill::fill_by_cross`
///    (`n_fill_by_cross` в `forms.csv`): крейт пути не отдаёт, `run_round`
///    определяет его по тому, что в буфере последних сделок шага нет сделки,
///    которая могла бы исполнить ногу (`trade_could_fill`).
///
/// Буфер последних сделок (`last_trades_capacity`) нужен только детектору
/// пути (3); читает его `run_round`, сразу после чтения очищая.
pub fn build_backtest(
    events: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
) -> Backtest<FastMarketDepth> {
    build_backtest_from(
        vec![DataSource::Data(Data::from_data(events))],
        exec_latency,
        move || FastMarketDepth::new(tick_size, lot_size),
        queue_model,
    )
}

/// Тот же движок, что `build_backtest`, но события **не копируются**: `Backtest`
/// читает их прямо из `events` и живёт только внутри `f`. Для сетки форм
/// (`lob bounce-grid`, S2): 48 `Backtest` над одними сутками держат одну копию
/// событий, а не 48 — с копиями восемь потоков × 15 млн × 64 Б валили процесс
/// («memory allocation of 963 859 520 bytes failed», ZEC день 2, 2026-09-18).
///
/// Почему это безопасно (проверено по исходнику `hftbacktest` 0.9.4,
/// `backtest/data/mod.rs`): `DataPtr::from_ptr` даёт `managed = false` — крейт
/// буфер не освобождает; `IndexMut` у `Data` объявлен, но в `backtest/` не
/// вызывается — буфер только читается; выравнивание `Vec<Event>` — `Event`,
/// как и требует `get_unchecked`. Время жизни держит замыкание: `Backtest` не
/// переживает `events`, потому что не покидает эту функцию.
pub fn with_backtest_over<R>(
    events: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
    f: impl FnOnce(&mut Backtest<FastMarketDepth>) -> R,
) -> R {
    // SAFETY: см. док выше — буфер жив до конца функции, крейт только читает.
    let data = unsafe { borrowed_data(events) };
    let mut bt = build_backtest_from(
        vec![DataSource::Data(data)],
        exec_latency,
        move || FastMarketDepth::new(tick_size, lot_size),
        queue_model,
    );
    let out = f(&mut bt);
    drop(bt);
    out
}

/// `Data` крейта поверх чужого среза без копии.
///
/// # Safety
/// `events` обязан пережить всё, что читает возвращённый `Data` (крейт его не
/// освобождает: `managed = false`, и не пишет — `IndexMut` в `backtest/` не
/// вызывается).
unsafe fn borrowed_data(events: &[Event]) -> Data<Event> {
    let bytes = std::ptr::slice_from_raw_parts_mut(
        events.as_ptr() as *mut u8,
        std::mem::size_of_val(events),
    );
    unsafe { Data::from_data_ptr(DataPtr::from_ptr(bytes), 0) }
}

/// Снимок книги движка `FastMarketDepth` (Э-05: копия `HashMapMarketDepth` крейта) в момент `t0`: уровни и **все** поля
/// лучших/крайних тиков (строит своя книга окон `WindowDepth` — те же поля, К3;
/// `of` — эталон сверки). Из него фабрика `depth` строителя собирает книгу
/// обеих сторон движка окна ровно той формы, что была бы у сплошного прогона
/// после тех же строк (перекрещённые «спрятанные» уровни и границы поиска
/// лучшей цены — тоже; пересобирать книгу событиями нельзя: порядок их
/// применения меняет, какая сторона окажется спрятана).
#[derive(Debug, Clone, PartialEq)]
pub struct DepthSnapshot {
    pub bids: Vec<(i64, f64)>,
    pub asks: Vec<(i64, f64)>,
    pub best_bid_tick: i64,
    pub best_ask_tick: i64,
    pub low_bid_tick: i64,
    pub high_ask_tick: i64,
    pub timestamp: i64,
}

impl DepthSnapshot {
    pub fn of(d: &FastMarketDepth) -> Self {
        let mut bids: Vec<(i64, f64)> = d.bid_depth.iter().collect();
        let mut asks: Vec<(i64, f64)> = d.ask_depth.iter().collect();
        bids.sort_unstable_by_key(|(t, _)| *t);
        asks.sort_unstable_by_key(|(t, _)| *t);
        Self {
            bids,
            asks,
            best_bid_tick: d.best_bid_tick,
            best_ask_tick: d.best_ask_tick,
            low_bid_tick: d.low_bid_tick,
            high_ask_tick: d.high_ask_tick,
            timestamp: d.timestamp,
        }
    }

    pub fn build(&self, tick_size: f64, lot_size: f64) -> FastMarketDepth {
        let mut d = FastMarketDepth::new(tick_size, lot_size);
        d.bid_depth.extend(self.bids.iter().copied());
        d.ask_depth.extend(self.asks.iter().copied());
        d.best_bid_tick = self.best_bid_tick;
        d.best_ask_tick = self.best_ask_tick;
        d.low_bid_tick = self.low_bid_tick;
        d.high_ask_tick = self.high_ask_tick;
        d.timestamp = self.timestamp;
        d
    }

    pub fn levels(&self) -> usize {
        self.bids.len() + self.asks.len()
    }
}

/// Окно одного сигнала: книга после строк `[0..m)` и срез с `m`, где `m` —
/// первая строка, у которой **хотя бы одна** метка (`local_ts` или `exch_ts`)
/// больше `t0`.
#[derive(Debug, Clone, PartialEq)]
pub struct SignalWindow {
    pub t0_ns: i64,
    pub start: usize,
    pub depth: DepthSnapshot,
}

/// Окна всех сигналов суток — **один** проход книги по событиям, общий для
/// всех форм (у них одни касания) и для **обеих** сторон движка.
///
/// У крейта две книги: локальная применяет строки по `local_ts`, биржевая —
/// по `exch_ts`, каждая в порядке записи до первой строки с меткой больше
/// текущего времени. В `t0` они разные: часы коллектора и биржи расходятся в
/// любую сторону (в записи 2026-09-12 локальные **отстают**, и снимок «по
/// `local_ts`» отдавал бирже строки из будущего — цена входа расходилась).
/// Поэтому снимок берётся после строк `[0..m)`, где `m` — первая строка с
/// `local_ts > t0` **или** `exch_ts > t0`; строки `[m..)` идут в срез, и
/// каждая сторона на первом же `elapse` доберёт из них свои (метка `<= t0`)
/// ровно по разу — итог на обеих сторонах тот же, что у сплошного прогона.
#[derive(Debug, Clone)]
pub struct SignalWindows {
    pub tick_size: f64,
    pub lot_size: f64,
    windows: Vec<SignalWindow>,
}

impl SignalWindows {
    /// Окна на своей книге целых тиков (`WindowDepth`, К3) — снимки те же, что у
    /// `build_crate`, по всем полям (разностные тесты и `bounce-grid --windows-check`).
    pub fn build<R: EventRows + ?Sized>(
        events: &R,
        t0s: &[i64],
        tick_size: f64,
        lot_size: f64,
    ) -> Self {
        Self::build_on(
            events,
            t0s,
            tick_size,
            lot_size,
            WindowDepth::new(tick_size, lot_size),
        )
    }

    /// Прежний путь — книга движка (`FastMarketDepth`, копия книги крейта) и `DepthSnapshot::of`: эталон
    /// разностной сверки К3 (условие Судьи b86eed6), в счёте не участвует.
    pub fn build_crate<R: EventRows + ?Sized>(
        events: &R,
        t0s: &[i64],
        tick_size: f64,
        lot_size: f64,
    ) -> Self {
        Self::build_on(
            events,
            t0s,
            tick_size,
            lot_size,
            FastMarketDepth::new(tick_size, lot_size),
        )
    }

    fn build_on<R: EventRows + ?Sized, B: WindowBook>(
        events: &R,
        t0s: &[i64],
        tick_size: f64,
        lot_size: f64,
        mut depth: B,
    ) -> Self {
        let mut t0s: Vec<i64> = t0s.to_vec();
        t0s.sort_unstable();
        t0s.dedup();
        let mut row = 0usize;
        let mut windows = Vec::with_capacity(t0s.len());
        for t0 in t0s {
            while row < events.len()
                && events.row_local_ts(row) <= t0
                && events.row_exch_ts(row) <= t0
            {
                depth.apply(&events.row(row));
                row += 1;
            }
            windows.push(SignalWindow {
                t0_ns: t0,
                start: row,
                depth: depth.snapshot(),
            });
        }
        Self {
            tick_size,
            lot_size,
            windows,
        }
    }

    /// Первое расхождение с другими окнами тех же `t0` (`None` — все снимки равны по всем
    /// полям): `t0` окна и что разошлось.
    pub fn first_mismatch(&self, other: &SignalWindows) -> Option<(i64, &'static str)> {
        if self.windows.len() != other.windows.len() {
            return Some((0, "число окон"));
        }
        self.windows.iter().zip(&other.windows).find_map(|(a, b)| {
            let (x, y) = (&a.depth, &b.depth);
            let field = if a.t0_ns != b.t0_ns {
                "t0"
            } else if a.start != b.start {
                "start"
            } else if x.bids != y.bids {
                "bids"
            } else if x.asks != y.asks {
                "asks"
            } else if x.best_bid_tick != y.best_bid_tick {
                "best_bid_tick"
            } else if x.best_ask_tick != y.best_ask_tick {
                "best_ask_tick"
            } else if x.low_bid_tick != y.low_bid_tick {
                "low_bid_tick"
            } else if x.high_ask_tick != y.high_ask_tick {
                "high_ask_tick"
            } else if x.timestamp != y.timestamp {
                "timestamp"
            } else {
                return None;
            };
            Some((a.t0_ns, field))
        })
    }

    pub fn window_at(&self, t0_ns: i64) -> Option<&SignalWindow> {
        let i = self.windows.partition_point(|w| w.t0_ns < t0_ns);
        self.windows.get(i).filter(|w| w.t0_ns == t0_ns)
    }

    pub fn len(&self) -> usize {
        self.windows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.windows.is_empty()
    }

    /// Уровней во всех снимках — оценка памяти окон (16 Б на уровень).
    pub fn levels_total(&self) -> usize {
        self.windows.iter().map(|w| w.depth.levels()).sum()
    }
}

/// Книга, которую `SignalWindows::build_on` проводит по событиям суток: своя (`WindowDepth`)
/// или эталон крейта.
trait WindowBook {
    fn apply(&mut self, ev: &Event);
    fn snapshot(&self) -> DepthSnapshot;
}

impl WindowBook for WindowDepth {
    fn apply(&mut self, ev: &Event) {
        // Порядок веток — как у крейта; строк очистки в нашем переводе нет
        // (`events_from_feed` шлёт явные нули).
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            self.update_bid_depth(ev.px, ev.qty);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
            self.update_ask_depth(ev.px, ev.qty);
        }
    }

    fn snapshot(&self) -> DepthSnapshot {
        WindowDepth::snapshot(self)
    }
}

impl WindowBook for FastMarketDepth {
    fn apply(&mut self, ev: &Event) {
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            self.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
            self.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
    }

    fn snapshot(&self) -> DepthSnapshot {
        DepthSnapshot::of(self)
    }
}

/// Движок одного окна: книга обеих сторон — из снимка, события — срез суток с
/// первой строки после `t0` (без копии), часы прибиты к `t0` строкой-якорем.
///
/// Якорь — нулевая заявка бида по цене 0: у `FastMarketDepth` это
/// заведомо пустой ход (тика 0 в книге нет, лучшему он не равен, границы
/// поиска при нулевом объёме не трогаются), но это событие ленты, и первое
/// `elapse(0)` ставит часы окна ровно на `t0` — как `elapse(t0 - now)` в
/// сплошном прогоне. Нулевая **сделка** якорем быть не может: при пустой
/// стороне книги крейт считает `price_tick - best_bid_tick` от `i64::MIN`.
// Восемь аргументов — цена параметров окна (книга, время, срез, тик, лот,
// задержка, модель очереди, замыкание); структура ради одного лишнего поля
// усложнила бы вызывающего сильнее, чем читается этот список.
#[allow(clippy::too_many_arguments)]
pub fn with_backtest_over_window<R>(
    depth: &DepthSnapshot,
    t0_ns: i64,
    rest: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
    f: impl FnOnce(&mut Backtest<FastMarketDepth>) -> R,
) -> R {
    let anchor = [Event {
        ev: LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
        exch_ts: t0_ns,
        local_ts: t0_ns,
        px: 0.0,
        qty: 0.0,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }];
    let mut sources = vec![DataSource::Data(Data::from_data(&anchor))];
    if !rest.is_empty() {
        // SAFETY: `rest` жив до конца функции, `Backtest` умирает раньше
        // (`drop(bt)` ниже); крейт только читает, см. `borrowed_data`.
        sources.push(DataSource::Data(unsafe { borrowed_data(rest) }));
    }
    let snap = depth.clone();
    let mut bt = build_backtest_from(
        sources,
        exec_latency,
        move || snap.build(tick_size, lot_size),
        queue_model,
    );
    let out = f(&mut bt);
    drop(bt);
    out
}

/// Общий низ трёх строителей: `QueueModelKind` выбирает пару «модель очереди +
/// модель исполнения» (`ExchangeKind`) движка. Три пути исполнения крейта —
/// в doc-комментарии `build_backtest`.
fn build_backtest_from(
    sources: Vec<DataSource<Event>>,
    exec_latency: ExecLatency,
    depth_builder: impl Fn() -> FastMarketDepth + 'static,
    queue_model: QueueModelKind,
) -> Backtest<FastMarketDepth> {
    // Обе ветки — одинаковый набор параметров, кроме пары очередь/исполнение:
    // `L2AssetBuilder` типизирован моделью очереди, поэтому ветка компилируется
    // в свой `Asset`, а `Backtest` стирает его в `dyn Processor`.
    let asset = match queue_model {
        QueueModelKind::RiskAdverse => L2AssetBuilder::default()
            .data(sources)
            .latency_model(MeasuredLatency(exec_latency))
            .asset_type(LinearAsset::new(1.0))
            .fee_model(TradingValueFeeModel::new(CommonFees::new(
                MAKER_FEE_BPS / 10_000.0,
                TAKER_FEE_BPS / 10_000.0,
            )))
            .last_trades_capacity(LAST_TRADES_CAPACITY)
            .queue_model(RiskAdverseQueueModel::new())
            .exchange(ExchangeKind::NoPartialFillExchange)
            .depth(depth_builder)
            .build()
            .unwrap(),
        QueueModelKind::Prob { n } => L2AssetBuilder::default()
            .data(sources)
            .latency_model(MeasuredLatency(exec_latency))
            .asset_type(LinearAsset::new(1.0))
            .fee_model(TradingValueFeeModel::new(CommonFees::new(
                MAKER_FEE_BPS / 10_000.0,
                TAKER_FEE_BPS / 10_000.0,
            )))
            .last_trades_capacity(LAST_TRADES_CAPACITY)
            .queue_model(ProbQueueModel::<PowerProbQueueFunc, FastMarketDepth>::new(
                PowerProbQueueFunc::new(n),
            ))
            .exchange(ExchangeKind::PartialFillExchange)
            .depth(depth_builder)
            .build()
            .unwrap(),
    };
    Backtest::builder().add_asset(asset).build().unwrap()
}

#[cfg(test)]
mod tests;
