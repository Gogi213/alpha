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
use hftbacktest::backtest::data::{Data, DataPtr};
use hftbacktest::backtest::models::{
    CommonFees, LatencyModel, RiskAdverseQueueModel, TradingValueFeeModel,
};
use hftbacktest::backtest::BacktestError;
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::depth::{HashMapMarketDepth, L2MarketDepth, MarketDepth};
use hftbacktest::types::{
    Bot, ElapseResult, Event, OrdType, Order, Side as HbtSide, Status, TimeInForce,
    EXCH_BID_DEPTH_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT,
};

use crate::lob::costs::{
    fill_rate, format_fill_column, leg_fee_bps, net_fill_bps, net_fill_interval, FillObservation,
    NetFillInterval, MAKER_FEE_BPS, TAKER_FEE_BPS,
};
use crate::lob::strategy::{on_event, Action, ExitReason, StrategyState, TradePlan};

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
    /// Вход исполнился тейкером (лимит пересёк книгу; у лестницы — хотя бы
    /// одна нога) — по флагу `maker` ордера крейта (В-63).
    pub entry_taker: bool,
    /// Выход исполнился тейкером: стоп/дедлайн/досрочный/трейл — по рынку,
    /// тейк — лимитом (мейкер); тоже по флагу крейта.
    pub exit_taker: bool,
}

/// Чистый результат круга в bps: направленная доходность минус комиссии
/// **по ногам** (В-63: `costs::leg_fee_bps` — мейкер 1.26 / тейкер 3.15 bps
/// после возврата; тейк лимитом — мейкер+мейкер 2.52, стоп по рынку —
/// мейкер+тейкер 4.41). `None` при неположительном входе или неконечных
/// ценах: отсутствие данных не есть нулевой результат (то же правило, что
/// неконечный markout в 5.2).
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

        let mut state = StrategyState::with_plan(
            asset_no,
            sig.sigma,
            cfg.order_qty,
            next_id,
            TradePlan::SpreadHold,
        );
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

        match run_round(bot, asset_no, &mut state, entry_id, 1, side)? {
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

enum FlattenOutcome {
    Flat,
    Retry,
    EndOfData,
}

/// Закрыть остаток позиции по рынку (IOC) и дождаться исполнения.
/// `Retry` — заявка стала терминальной, а позиция осталась (нет ликвидности
/// на лучшей цене): вызывающий шлёт новую с новым `order_id`.
fn flatten_residual<B, MD>(
    bot: &mut B,
    asset_no: usize,
    order_id: u64,
) -> Result<FlattenOutcome, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let pos = bot.position(asset_no);
    let d = bot.depth(asset_no);
    let (bid, ask) = (d.best_bid(), d.best_ask());
    let ok_px = |px: f64| px.is_finite() && px > 0.0;
    if pos > 0.0 {
        if !ok_px(bid) {
            return match bot.elapse(ON_EVENT_POLL_STEP_NS)? {
                ElapseResult::EndOfData => Ok(FlattenOutcome::EndOfData),
                _ => Ok(FlattenOutcome::Retry),
            };
        }
        bot.submit_sell_order(
            asset_no,
            order_id,
            bid,
            pos,
            TimeInForce::IOC,
            OrdType::Market,
            false,
        )?;
    } else {
        if !ok_px(ask) {
            return match bot.elapse(ON_EVENT_POLL_STEP_NS)? {
                ElapseResult::EndOfData => Ok(FlattenOutcome::EndOfData),
                _ => Ok(FlattenOutcome::Retry),
            };
        }
        bot.submit_buy_order(
            asset_no,
            order_id,
            ask,
            -pos,
            TimeInForce::IOC,
            OrdType::Market,
            false,
        )?;
    }
    loop {
        if bot.elapse(ON_EVENT_POLL_STEP_NS)? == ElapseResult::EndOfData {
            return Ok(FlattenOutcome::EndOfData);
        }
        if bot.position(asset_no) == 0.0 {
            return Ok(FlattenOutcome::Flat);
        }
        let terminal = !matches!(
            bot.orders(asset_no).get(&order_id).map(|o| o.status),
            Some(Status::New) | Some(Status::PartiallyFilled)
        );
        if terminal {
            return Ok(FlattenOutcome::Retry);
        }
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
    /// Сколько раз цена входа в момент отправки **пересекала** спред (для
    /// покупки — `ask ≤ entry_px`): именно эти заявки пост-онли отвергает.
    pub entry_crossed: u64,
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
}

/// Сколько ног входа ставит этот план: у Decision 20 — одна, у лестницы
/// В-44 — `grid_legs`. Нужно драйверу, чтобы найти исполненную ногу.
fn legs_of(plan: TradePlan) -> u8 {
    match plan {
        TradePlan::SpreadHold => 1,
        TradePlan::Bounce { grid_legs, .. } => grid_legs.max(1),
    }
}

/// Итог одного круга в терминах драйвера.
enum RoundOutcome {
    /// Круг закрыт: обе ноги исполнены, причина выхода известна.
    Filled {
        fill: Fill,
        exit_ts: i64,
        reason: ExitReason,
    },
    /// Вход не исполнился за время жизни плана и снят. Статус входного ордера
    /// несётся наружу: `Rejected` — это отвергнутая заявка (например,
    /// пост-онли, пересекающая спред), а не «просто не дошло» — разница
    /// ровно та, ради которой делается замер.
    TimedOut { entry_status: Option<Status> },
    /// Данные кончились посреди круга.
    EndOfData,
    /// `Idle` без выхода и без таймаута — рассинхрон с данными.
    Inconsistent,
}

/// Крутит один круг: продвигает часы стороны шагами `ON_EVENT_POLL_STEP_NS`,
/// пока `on_event` не приведёт состояние к `Idle`, и достаёт из `Bot<MD>`
/// исполнение обеих ног. Общий для обоих планов (`SpreadHold` — Decision 20,
/// `Bounce` — В-44): свой цикл решений у второго плана был бы второй
/// стратегией.
fn run_round<B, MD>(
    bot: &mut B,
    asset_no: usize,
    state: &mut StrategyState,
    entry_id: u64,
    legs: u8,
    side: HbtSide,
) -> Result<RoundOutcome, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut timed_out = false;
    let mut exit: Option<(u64, ExitReason)> = None;
    loop {
        if bot.elapse(ON_EVENT_POLL_STEP_NS)? == ElapseResult::EndOfData {
            return Ok(RoundOutcome::EndOfData);
        }
        match on_event(bot, state)? {
            Action::EntryTimedOut { .. } => timed_out = true,
            Action::ExitSubmitted {
                order_id, reason, ..
            } => exit = Some((order_id, reason)),
            Action::Idle | Action::EntrySubmitted { .. } => {}
        }
        if state.is_idle() {
            break;
        }
    }
    if timed_out {
        let entry_status = bot.orders(asset_no).get(&entry_id).map(|o| o.status);
        return Ok(RoundOutcome::TimedOut { entry_status });
    }
    let Some((exit_id, reason)) = exit else {
        return Ok(RoundOutcome::Inconsistent);
    };
    // Лестница ставит несколько ног равного размера, и исполниться может не
    // одна (агрессор выедает уровни подряд): цена входа — **среднее** цен
    // исполненных ног с равными весами. Именно среднее, а не цена одной ноги:
    // у драйвера одна `Fill` на круг, и цена одной ноги исказила бы `net`.
    let filled_legs: Vec<(f64, bool)> = (0..legs.max(1) as u64)
        .filter_map(|i| bot.orders(asset_no).get(&entry_id.saturating_add(i)))
        .filter(|o| o.status == Status::Filled)
        .map(|o| (o.exec_price(), o.maker))
        .collect();
    let entry_px = if filled_legs.is_empty() {
        None
    } else {
        Some(
            filled_legs.iter().map(|(px, _)| *px).sum::<f64>()
                / crate::stats::count_f64(filled_legs.len()),
        )
    };
    // Комиссия ноги — по флагу `maker` ордера крейта (В-63): у лестницы вход
    // тейкерский, если тейкером исполнилась хотя бы одна нога (консервативно).
    let entry_taker = filled_legs.iter().any(|(_, maker)| !maker);
    let exit_info = bot
        .orders(asset_no)
        .get(&exit_id)
        .filter(|o| o.status == Status::Filled)
        .map(|o| (o.exec_price(), o.exch_timestamp, o.maker));
    match (entry_px, exit_info) {
        (Some(entry_px), Some((exit_px, exit_ts, exit_maker))) => {
            let dir = if side == HbtSide::Buy { 1 } else { -1 };
            Ok(RoundOutcome::Filled {
                fill: Fill {
                    dir,
                    entry_px,
                    exit_px,
                    qty: state.qty(),
                    entry_taker,
                    exit_taker: !exit_maker,
                },
                exit_ts,
                reason,
            })
        }
        _ => Ok(RoundOutcome::Inconsistent),
    }
}

/// Шаг драйвера на одном сигнале — всё, что требует движка: часы к `t0`,
/// проверка позиции, вход, круг, страховка остатка. Общий для сплошного
/// прогона (`drive_bounce`) и прогона по сетапам (`drive_bounce_windowed`):
/// различие только в том, откуда берётся движок, — иначе это была бы вторая
/// стратегия.
enum SignalStep {
    /// Часы не дошли до `t0`: запись кончилась.
    EndOfData,
    /// В `t0` позиция уже открыта.
    Busy,
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
    },
}

fn drive_signal<B, MD>(
    bot: &mut B,
    asset_no: usize,
    sig: &BounceSignal,
    cfg: &DriveConfig,
    next_id: &mut u64,
) -> Result<SignalStep, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let now = bot.current_timestamp();
    if sig.t0_ns > now && bot.elapse(sig.t0_ns - now)? == ElapseResult::EndOfData {
        return Ok(SignalStep::EndOfData);
    }
    if bot.position(asset_no) != 0.0 {
        // Тот же пропуск «позиция занята», но пойманный по факту открытой
        // позиции, а не по `blocked_until_ns` (он короче жизни позиции:
        // момент выхода не равен времени закрытия). В отчёт идут оба.
        return Ok(SignalStep::Busy);
    }

    let mut state =
        StrategyState::with_plan(asset_no, sig.sigma, cfg.order_qty, *next_id, sig.plan);
    *next_id = next_id.saturating_add(4);

    let (entry_id, side) = match on_event(bot, &mut state)? {
        Action::EntrySubmitted { order_id, side, .. } => (order_id, side),
        _ => {
            return Ok(SignalStep::NotSubmitted {
                idle_ns: bot.current_timestamp(),
            })
        }
    };
    // Замер механизма отказа (таск 38): в момент отправки входа смотрим,
    // пересекает ли его цена спред и каков спред. Пост-онли такую заявку
    // отвергает, обычный лимит — исполняет как тейкер.
    let mut crossed = false;
    let mut spread = None;
    if let TradePlan::Bounce { entry_px, .. } = sig.plan {
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

    let outcome = run_round(bot, asset_no, &mut state, entry_id, legs_of(sig.plan), side)?;
    if matches!(outcome, RoundOutcome::EndOfData) {
        return Ok(SignalStep::Submitted {
            crossed,
            spread,
            outcome,
            residual: None,
            idle_ns: bot.current_timestamp(),
        });
    }
    // Страховка (2026-09-18): круг кончился, а позиция осталась (например,
    // `Inconsistent`) — закрыть по рынку и посчитать, иначе все дальнейшие
    // сигналы молча «заняты» и остаток суток не считается вовсе.
    let mut residual = None;
    if bot.position(asset_no) != 0.0 {
        let mut ended = false;
        while bot.position(asset_no) != 0.0 {
            let id = *next_id;
            *next_id = next_id.saturating_add(1);
            match flatten_residual(bot, asset_no, id)? {
                FlattenOutcome::Flat => break,
                FlattenOutcome::Retry => continue,
                FlattenOutcome::EndOfData => {
                    ended = true;
                    break;
                }
            }
        }
        residual = Some(ended);
        if ended {
            return Ok(SignalStep::Submitted {
                crossed,
                spread,
                outcome,
                residual,
                idle_ns: bot.current_timestamp(),
            });
        }
    }
    bot.clear_inactive_orders(Some(asset_no));
    Ok(SignalStep::Submitted {
        crossed,
        spread,
        outcome,
        residual,
        idle_ns: bot.current_timestamp(),
    })
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
            entry_crossed: 0,
            spread_at_entry: Vec::new(),
            submitted_signal: Vec::new(),
            busy_signal: Vec::new(),
            busy_wait_ns_max: 0,
            round_ns_max: 0,
            misses: MissLedger::default(),
            observations: Vec::new(),
            incomplete: true,
            residual_flattened: 0,
        }
    }
}

/// Цикл по сигналам, общий для обоих драйверов. `source(sig, step)` даёт шагу
/// движок и возвращает его результат; `None` — для сигнала нет данных (там,
/// где сплошной прогон упёрся бы в конец записи). Учёт кругов, промахов и
/// причин выхода — здесь, движка он не касается.
fn drive_bounce_with<B, MD, S>(
    asset_no: usize,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    mut source: S,
) -> Result<BounceRun, B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
    S: FnMut(
        &BounceSignal,
        &mut dyn FnMut(&mut B) -> Result<SignalStep, B::Error>,
    ) -> Result<Option<SignalStep>, B::Error>,
{
    let mut order: Vec<BounceSignal> = signals.to_vec();
    order.sort_by_key(|s| s.t0_ns);
    let profile = signals.first().map(|s| s.profile).unwrap_or(0);
    let mut fills: Vec<Fill> = Vec::new();
    let mut misses = MissLedger::default();
    let mut observations: Vec<FillObservation> = Vec::new();
    let mut exits = ExitTally::default();
    let mut entry_rejected: u64 = 0;
    let mut entry_crossed: u64 = 0;
    let mut spread_at_entry: Vec<f64> = Vec::new();
    let mut busy_signal: Vec<usize> = Vec::new();
    let mut submitted_signal: Vec<usize> = Vec::new();
    let mut busy_wait_ns_max: i64 = 0;
    let mut round_ns_max: i64 = 0;
    let mut fill_signal: Vec<usize> = Vec::new();
    let mut fill_reason: Vec<ExitReason> = Vec::new();
    let mut fill_exit_ns: Vec<i64> = Vec::new();
    let mut next_id = cfg.first_order_id;
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

    for (sig_idx, sig) in order.iter().enumerate() {
        if entry_side(sig.sigma).is_none() {
            continue;
        }
        if sig.t0_ns < idle_ns {
            busy_signal.push(sig_idx);
            busy_wait_ns_max = busy_wait_ns_max.max(idle_ns.saturating_sub(sig.t0_ns));
            misses.record(MissReason::PositionBusy);
            observations.push(miss_observation(sig.t0_ns));
            continue;
        }
        let step = source(sig, &mut |bot: &mut B| {
            drive_signal(bot, asset_no, sig, cfg, &mut next_id)
        })?;
        let Some(step) = step else {
            incomplete = true;
            break;
        };
        match step {
            SignalStep::EndOfData => {
                incomplete = true;
                break;
            }
            SignalStep::Busy => {
                busy_signal.push(sig_idx);
                misses.record(MissReason::PositionBusy);
                observations.push(miss_observation(sig.t0_ns));
            }
            SignalStep::NotSubmitted { idle_ns: idle } => {
                idle_ns = idle;
                misses.record(MissReason::EntryTimeout);
                observations.push(miss_observation(sig.t0_ns));
            }
            SignalStep::Submitted {
                crossed,
                spread,
                outcome,
                residual,
                idle_ns: idle,
            } => {
                idle_ns = idle;
                submitted_signal.push(sig_idx);
                if crossed {
                    entry_crossed = entry_crossed.saturating_add(1);
                }
                if let Some(s) = spread {
                    spread_at_entry.push(s);
                }
                match outcome {
                    RoundOutcome::EndOfData => {
                        incomplete = true;
                        break;
                    }
                    RoundOutcome::Inconsistent => incomplete = true,
                    RoundOutcome::TimedOut { entry_status } => {
                        if matches!(entry_status, Some(Status::Rejected)) {
                            entry_rejected = entry_rejected.saturating_add(1);
                        }
                        misses.record(MissReason::EntryTimeout);
                        observations.push(miss_observation(sig.t0_ns));
                    }
                    RoundOutcome::Filled {
                        fill,
                        exit_ts,
                        reason,
                    } => {
                        match reason {
                            ExitReason::Take => exits.take += 1,
                            ExitReason::Stop => exits.stop += 1,
                            ExitReason::Deadline => exits.deadline += 1,
                            ExitReason::Horizon => exits.horizon += 1,
                            ExitReason::Trail => exits.trail += 1,
                            ExitReason::Early => exits.early += 1,
                        }
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
                        break;
                    }
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
        entry_crossed,
        spread_at_entry,
        submitted_signal,
        busy_signal,
        busy_wait_ns_max,
        round_ns_max,
        misses,
        observations,
        incomplete,
        residual_flattened,
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
    let run = drive_bounce_with::<B, MD, _>(asset_no, signals, cfg, |_, step| step(bot).map(Some))?;
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
/// Точность: снимок — та же `HashMapMarketDepth` крейта после тех же строк
/// (обе метки `<= t0`, см. `SignalWindows`), со всеми полями лучших/крайних
/// тиков, один на обе стороны движка; строки с одной меткой за `t0` каждая
/// сторона доберёт из среза сама по разу, как и в сплошном прогоне; часы
/// окна прибиты к `t0` строкой-якорем. Гейт — побайтово те же круги, что у
/// `drive_bounce` (тест и `--driver full`).
pub fn drive_bounce_windowed(
    events: &[Event],
    windows: &SignalWindows,
    signals: &[BounceSignal],
    cfg: &DriveConfig,
    exec_latency: ExecLatency,
) -> Result<BounceRun, BacktestError> {
    drive_bounce_with::<Backtest<HashMapMarketDepth>, HashMapMarketDepth, _>(
        0,
        signals,
        cfg,
        |sig, step| {
            let Some(w) = windows.window_at(sig.t0_ns) else {
                return Ok(None);
            };
            if w.start >= events.len() {
                // Строк после `t0` нет — сплошной прогон здесь упёрся бы в
                // конец записи, не дойдя до сигнала.
                return Ok(None);
            }
            with_backtest_over_window(
                &w.depth,
                sig.t0_ns,
                &events[w.start..],
                windows.tick_size,
                windows.lot_size,
                exec_latency,
                |bt| {
                    if bt.elapse(0)? == ElapseResult::EndOfData {
                        return Ok(SignalStep::EndOfData);
                    }
                    step(bt)
                },
            )
            .map(Some)
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
    exec_latency: ExecLatency,
) -> Backtest<HashMapMarketDepth> {
    build_backtest_from(
        vec![DataSource::Data(Data::from_data(events))],
        exec_latency,
        move || HashMapMarketDepth::new(tick_size, lot_size),
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
    f: impl FnOnce(&mut Backtest<HashMapMarketDepth>) -> R,
) -> R {
    // SAFETY: см. док выше — буфер жив до конца функции, крейт только читает.
    let data = unsafe { borrowed_data(events) };
    let mut bt = build_backtest_from(vec![DataSource::Data(data)], exec_latency, move || {
        HashMapMarketDepth::new(tick_size, lot_size)
    });
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

/// Снимок `HashMapMarketDepth` крейта в момент `t0`: уровни и **все** поля
/// лучших/крайних тиков. Из него фабрика `depth` строителя собирает книгу
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
    pub fn of(d: &HashMapMarketDepth) -> Self {
        let mut bids: Vec<(i64, f64)> = d.bid_depth.iter().map(|(t, q)| (*t, *q)).collect();
        let mut asks: Vec<(i64, f64)> = d.ask_depth.iter().map(|(t, q)| (*t, *q)).collect();
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

    pub fn build(&self, tick_size: f64, lot_size: f64) -> HashMapMarketDepth {
        let mut d = HashMapMarketDepth::new(tick_size, lot_size);
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
    pub fn build(events: &[Event], t0s: &[i64], tick_size: f64, lot_size: f64) -> Self {
        let mut t0s: Vec<i64> = t0s.to_vec();
        t0s.sort_unstable();
        t0s.dedup();
        let mut depth = HashMapMarketDepth::new(tick_size, lot_size);
        let mut row = 0usize;
        let mut windows = Vec::with_capacity(t0s.len());
        for t0 in t0s {
            while row < events.len() && events[row].local_ts <= t0 && events[row].exch_ts <= t0 {
                let ev = &events[row];
                // Порядок веток — как у крейта; строк очистки в нашем
                // переводе нет (`events_from_feed` шлёт явные нули).
                if ev.is(LOCAL_BID_DEPTH_EVENT) {
                    depth.update_bid_depth(ev.px, ev.qty, ev.local_ts);
                } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
                    depth.update_ask_depth(ev.px, ev.qty, ev.local_ts);
                }
                row += 1;
            }
            windows.push(SignalWindow {
                t0_ns: t0,
                start: row,
                depth: DepthSnapshot::of(&depth),
            });
        }
        Self {
            tick_size,
            lot_size,
            windows,
        }
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

/// Движок одного окна: книга обеих сторон — из снимка, события — срез суток с
/// первой строки после `t0` (без копии), часы прибиты к `t0` строкой-якорем.
///
/// Якорь — нулевая заявка бида по цене 0: у `HashMapMarketDepth` это
/// заведомо пустой ход (тика 0 в книге нет, лучшему он не равен, границы
/// поиска при нулевом объёме не трогаются), но это событие ленты, и первое
/// `elapse(0)` ставит часы окна ровно на `t0` — как `elapse(t0 - now)` в
/// сплошном прогоне. Нулевая **сделка** якорем быть не может: при пустой
/// стороне книги крейт считает `price_tick - best_bid_tick` от `i64::MIN`.
pub fn with_backtest_over_window<R>(
    depth: &DepthSnapshot,
    t0_ns: i64,
    rest: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    f: impl FnOnce(&mut Backtest<HashMapMarketDepth>) -> R,
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
    let mut bt = build_backtest_from(sources, exec_latency, move || {
        snap.build(tick_size, lot_size)
    });
    let out = f(&mut bt);
    drop(bt);
    out
}

fn build_backtest_from(
    sources: Vec<DataSource<Event>>,
    exec_latency: ExecLatency,
    depth_builder: impl Fn() -> HashMapMarketDepth + 'static,
) -> Backtest<HashMapMarketDepth> {
    Backtest::builder()
        .add_asset(
            L2AssetBuilder::default()
                .data(sources)
                .latency_model(MeasuredLatency(exec_latency))
                .asset_type(LinearAsset::new(1.0))
                .fee_model(TradingValueFeeModel::new(CommonFees::new(
                    MAKER_FEE_BPS / 10_000.0,
                    TAKER_FEE_BPS / 10_000.0,
                )))
                .queue_model(RiskAdverseQueueModel::new())
                .exchange(ExchangeKind::NoPartialFillExchange)
                .depth(depth_builder)
                .build()
                .unwrap(),
        )
        .build()
        .unwrap()
}

#[cfg(test)]
mod tests;
