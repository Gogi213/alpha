//! `lob react` — замер реакционного пути и гейт G-LAT (таск 15, история 14,
//! `PLAN.md` 3.1/6, D-РЕАКЦИЯ, D-ОРДЕР, D-HOR).
//!
//! Тонкая CLI-обёртка вокруг `run_react_over_feed` — ядра, которое не знает,
//! живой у него `Feed` или сценарный (`interfaces.md`: «вызывающий код держит
//! `&mut dyn Feed` и не отличает источник», тот же приём, что `lob session`).
//! Ядро покрыто тестами на сценарном `Feed` без сети; живой прогон — ручной,
//! ≤ 5 минут (`CLAUDE.md`, R78).
//!
//! Дистанция между рекордером и решением, которую этот таск обязан закрыть
//! (бриф): пять меток одной цепочки на каждое обновление стакана —
//! `recv` (`local_ts_ns` из `Feed`), `разбор` (`parse_latency_ns` из `Feed`,
//! таск 04), `книга` (после `Book::apply`), `триггер` (решение — сменился ли
//! лучший тик своей стороны), `send` (dry-run: метка вместо кадра сокету).
//! Все метки, кроме `recv`/`разбор` (те приходят уже готовыми от `Feed`), —
//! через `bybit::clock::MonotonicClock`, не системные часы (`interfaces.md`,
//! запрет 2): грепом проверено ниже.
//!
//! **D-ОРДЕР без реализации второй стратегии.** «Триггер» здесь — не
//! экономический сигнал `lob/strategy::on_event` (уровень densities решает
//! `lob/levels`, вне зоны этого таска), а перемена лучшего тика своей
//! стороны спреда: самый частый повод в живом потоке дать ≥ 1000
//! срабатываний за пять минут и мерить p99 на статистически осмысленной
//! выборке — настоящий сигнал по плотности живёт секунды и такой выборки за
//! пять минут не даст.
//!
//! **Запрет 1 горячего пути (дозапрос ревью таска 15).** Готовый ордер
//! (`bybit::trade_ws::ReadyMakerOrder`) пересобирается и переподписывается
//! (`.rebuild(...)`) **только внутри `if changed`** — на событии, где цена
//! своей стороны спреда реально сдвинулась, не на каждое обновление книги:
//! иначе даже буферы `ReadyMakerOrder` не спасли бы от подписи на каждый тик.
//! `send_ready_maker_order` в ветке срабатывания не вызывается вовсе — dry-run
//! заменяет `send` меткой часов (критерий приёмки: «ни одного ордера»), и
//! единственная работа, которую делает ветка срабатывания, — прочитать уже
//! готовый `ReadyMakerOrder`, ничего в нём не трогая.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::book::Book;
use crate::bybit::clock::MonotonicClock;
use crate::bybit::conn::{Clock, SystemClock};
use crate::bybit::probe::{self, OrderSide, DEFAULT_RECV_WINDOW_MS};
use crate::bybit::sign::Credentials;
use crate::bybit::trade_ws::{OrderSigner, ReadyMakerOrder};
use crate::feed::live::{LiveFeed, PoolMember};
use crate::feed::{Event, Feed};
use crate::lob::markout::HORIZONS_MS;

/// Потолок живого прогона (`CLAUDE.md`, R78 задачи: «любой тестовый прогон —
/// не дольше 5 минут»). Не аргумент с большим умолчанием — жёсткий потолок,
/// `--minutes` больше него отклоняется.
pub const MAX_MINUTES: u64 = 5;

/// Минимум срабатываний для объявления гейта G-LAT (критерий приёмки таска
/// 15, дословно «≥ 1000 срабатываний»). Меньше — печатается честно,
/// `debug`, гейт не объявляется (тот же приём, что `lob levels`/`markout` на
/// коротких отладочных прогонах).
pub const MIN_TRIGGERS_FOR_GATE: u64 = 1000;

/// Бюджет всего реакционного пути — гейт **G-LAT** (`PLAN.md` 3.1/6.1,
/// D-РЕАКЦИЯ): `p99 < 5 мс`, единственный бюджет этого таска, который не
/// пересматривается по замеру (суббюджеты этапов — калибровка, этот — нет).
pub const G_LAT_BUDGET_NS: i64 = 5_000_000;

/// Порог недостижимости горизонта (D-HOR, `PLAN.md` 4.2): `p99` реакции
/// больше 20% длины горизонта — горизонт помечается `unreachable` и выходит
/// из вердикта. Число из плана, не изобретено здесь.
const UNREACHABLE_THRESHOLD: f64 = 0.20;

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Аргументы `lob react`. Ни у `symbol`/`tick_e9`/`step_e9`/`qty_e9` нет
/// правдоподобного умолчания (правило 1 `interfaces.md`) — те же данные, что
/// несёт строка `instruments.csv` последнего `lob pick`, владелец называет
/// явно, как уже делает `lob probe`.
#[derive(Debug, Args)]
pub struct ReactArgs {
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// `priceFilter.tickSize` инструмента, в 1e-9.
    #[arg(long)]
    pub tick_e9: i64,
    /// `lotSizeFilter.qtyStep` инструмента, в 1e-9.
    #[arg(long)]
    pub step_e9: i64,
    /// Сторона наблюдаемого мейкер-ордера: `buy` — у лучшего бида, `sell` —
    /// у лучшего аска (D-ОРДЕР: «своя сторона спреда»).
    #[arg(long, default_value = "buy")]
    pub side: String,
    /// `lotSizeFilter.minOrderQty` инструмента, в 1e-9 (готовый payload —
    /// тот же размер, что войдёт в ордер, D-ОРДЕР).
    #[arg(long)]
    pub qty_e9: i64,
    /// Окно подписи Bybit по умолчанию — пять секунд (документация биржи,
    /// тот же дефолт, что `lob probe`).
    #[arg(long, default_value_t = DEFAULT_RECV_WINDOW_MS)]
    pub recv_window_ms: u32,
    /// Длина живого прогона, минуты. Потолок `MAX_MINUTES` — не умолчание,
    /// а жёсткая граница (`CLAUDE.md`, R78).
    #[arg(long, default_value_t = MAX_MINUTES)]
    pub minutes: u64,
    /// Корень для `react-<symbol>.csv` по умолчанию.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Куда писать строки срабатываний (по умолчанию `<root>/react-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Идентификатор хоста для предрегистрации (D-ХОСТ) — владелец называет
    /// явно; без него печатается то, что реально известно (переменные
    /// окружения ОС), не изобретённая строка.
    #[arg(long)]
    pub host_id: Option<String>,
    /// `probe-<symbol>.csv` последнего `lob probe` — RTT до биржи рядом с
    /// G-LAT в предрегистрации (D-RTT). Без файла печатается честно: RTT не
    /// измерен этим прогоном.
    #[arg(long)]
    pub probe_csv: Option<PathBuf>,
}

fn parse_side(s: &str) -> anyhow::Result<OrderSide> {
    match s.to_ascii_lowercase().as_str() {
        "buy" => Ok(OrderSide::Buy),
        "sell" => Ok(OrderSide::Sell),
        other => Err(anyhow::anyhow!(
            "сторона обязана быть buy или sell, получено {other}"
        )),
    }
}

fn resolve_host_id(explicit: Option<&str>) -> String {
    if let Some(h) = explicit {
        return h.to_string();
    }
    std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "unknown-host".to_string())
}

// ---------------------------------------------------------------------------
// Измерение одного срабатывания.
// ---------------------------------------------------------------------------

/// Пять меток одного срабатывания, наносекунды. `parse_ns` — `None`, когда
/// `Feed` не измерил разбор (`feed::Event::Market::parse_latency_ns`,
/// `Some` только в живом режиме, `interfaces.md`): печатать здесь ноль
/// значило бы измеренное там, где ничего не измерялось (правило 1).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ReactSample {
    pub parse_ns: Option<i64>,
    pub book_ns: i64,
    pub trigger_ns: i64,
    pub order_ns: i64,
    pub full_ns: i64,
}

/// Медиана и p99 одной стадии — `None` на пустой выборке (правило 1: нечего
/// измерять — нечего печатать).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StageSummary {
    pub n: usize,
    pub median_ns: i64,
    pub p99_ns: i64,
}

fn summarize_stage(samples: &[i64]) -> Option<StageSummary> {
    if samples.is_empty() {
        return None;
    }
    Some(StageSummary {
        n: samples.len(),
        median_ns: probe::percentile_ns(samples, 50),
        p99_ns: probe::percentile_ns(samples, 99),
    })
}

/// Достижимость одного горизонта (D-HOR): `p99` полного пути против 20% его
/// длины.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HorizonReach {
    pub horizon_ms: i64,
    pub reachable: bool,
}

fn horizon_reach(full_path_p99_ns: i64) -> Vec<HorizonReach> {
    HORIZONS_MS
        .iter()
        .map(|&h_ms| {
            let threshold_ns = (h_ms as f64 * 1_000_000.0 * UNREACHABLE_THRESHOLD) as i64;
            HorizonReach {
                horizon_ms: h_ms,
                reachable: full_path_p99_ns <= threshold_ns,
            }
        })
        .collect()
}

/// Итог прогона: то, что печатается и уходит в CSV.
#[derive(Debug, Clone, PartialEq)]
pub struct ReactReport {
    pub events: u64,
    pub triggers: u64,
    pub gated: bool,
    pub parse: Option<StageSummary>,
    pub book: Option<StageSummary>,
    pub trigger: Option<StageSummary>,
    pub order: Option<StageSummary>,
    pub full_path: Option<StageSummary>,
    pub g_lat_pass: Option<bool>,
    pub horizons: Vec<HorizonReach>,
    pub host_id: String,
    pub probe_rtt_ns: Option<(i64, i64)>,
    pub out: PathBuf,
}

/// Конфигурация ядра — то, что не зависит от источника `Feed`.
pub struct ReactCoreConfig {
    pub tick_e9: i64,
    pub step_e9: i64,
    pub side: OrderSide,
    pub qty_e9: i64,
    pub recv_window_ms: u32,
}

/// Состояние ядра между событиями — книга и готовый ордер живут здесь, а
/// не в локальных переменных цикла: тест на аллокации (`process_event_
/// allocates_nothing_after_warmup`, зона трейд_ws) обязан звать одну и ту же
/// обработку события 10⁶ раз на ОДНОМ и том же состоянии, иначе буферы
/// `ReadyMakerOrder` пересоздавались бы на каждый вызов и измеряли не то.
pub struct ReactCoreState {
    book: Book,
    last_top: Option<(i64, i64)>,
    ready: ReadyMakerOrder,
    ready_warm: bool,
    req_counter: u64,
    req_id_buf: String,
}

impl ReactCoreState {
    pub fn new(tick_e9: i64, step_e9: i64) -> Self {
        Self {
            book: Book::new(tick_e9, step_e9),
            last_top: None,
            ready: ReadyMakerOrder::new(),
            ready_warm: false,
            req_counter: 0,
            req_id_buf: String::with_capacity(48),
        }
    }
}

/// Обрабатывает одно событие `Feed`, дописывая в `samples` образец при
/// срабатывании. Вынесена из `run_react_over_feed`, чтобы тест на
/// аллокации мог измерять именно этот шаг в цикле, как `commands::lob::
/// session::write_market_event` уже делает для рекордера.
///
/// **Запрет 1 (дозапрос ревью):** `ready.rebuild(...)` вызывается **только**
/// внутри `if changed` — на событии, где цена своей стороны спреда реально
/// сдвинулась, а не на каждое обновление книги. `req_id_buf` — тот же приём,
/// что буферы `ReadyMakerOrder`: `.clear()` и `write!` вместо `format!`,
/// иначе счётчик `req_id` сам стал бы источником аллокации на каждый ребилд.
pub fn process_event<S: OrderSigner>(
    state: &mut ReactCoreState,
    cfg: &ReactCoreConfig,
    signer: &S,
    symbol: &str,
    stage_clock: &MonotonicClock,
    event: Event,
    samples: &mut Vec<ReactSample>,
) {
    let Event::Market {
        local_ts_ns,
        parse_latency_ns,
        payload,
        ..
    } = event
    else {
        return;
    };
    let t_recv = local_ts_ns;
    let t_parsed = t_recv.saturating_add(parse_latency_ns.unwrap_or(0));
    let crate::bybit::ws::Event::Book(update) = payload else {
        return;
    };
    if state.book.apply(&update).is_err() {
        return;
    }
    let t_book = stage_clock.now_ns();
    let (Some(bid), Some(ask)) = (
        state.book.best_bid_tick_opt(),
        state.book.best_ask_tick_opt(),
    ) else {
        return;
    };
    let top = (bid, ask);
    let changed = state.last_top != Some(top);
    if !changed {
        return;
    }
    if state.ready_warm {
        let t_trigger = stage_clock.now_ns();
        // Dry-run: `send` заменён меткой — ни кадра транспорту, ни ордера
        // (критерий приёмки). `state.ready` не трогается здесь ничем: ни
        // одного поля не читаем, кроме факта, что он уже готов.
        let t_send = stage_clock.now_ns();
        samples.push(ReactSample {
            parse_ns: parse_latency_ns,
            book_ns: t_book.saturating_sub(t_parsed),
            trigger_ns: t_trigger.saturating_sub(t_book),
            order_ns: t_send.saturating_sub(t_trigger),
            full_ns: t_send.saturating_sub(t_recv),
        });
    }
    state.last_top = Some(top);

    // Готовим ордер на СЛЕДУЮЩЕЕ срабатывание — за пределами измеряемых
    // стадий текущего события (D-ОРДЕР), и только теперь, когда цена
    // реально изменилась (запрет 1: не на каждое обновление книги).
    let price_e9 = match cfg.side {
        OrderSide::Buy => bid.saturating_mul(cfg.tick_e9),
        OrderSide::Sell => ask.saturating_mul(cfg.tick_e9),
    };
    state.req_counter += 1;
    state.req_id_buf.clear();
    {
        use std::fmt::Write as _;
        let _ = write!(state.req_id_buf, "react-{}", state.req_counter);
    }
    let timestamp_ms = SystemClock.now_ns() / 1_000_000;
    if state
        .ready
        .rebuild(
            signer,
            symbol,
            cfg.side,
            cfg.qty_e9,
            price_e9,
            cfg.recv_window_ms,
            &state.req_id_buf,
            timestamp_ms,
        )
        .is_ok()
    {
        state.ready_warm = true;
    }
}

/// Ядро замера: читает `Feed` до `None`/сигнала остановки, ведёт книгу,
/// готовит и (dry-run) «отправляет» мейкер-ордер на каждую смену лучшего
/// тика своей стороны. Не делает сети и не пишет файлов — только измеряет;
/// вызывающий (`run_react`, тесты) решает, что делать дальше с образцами.
///
/// `should_stop` зовётся после каждого события — источник решает, живой
/// дедлайн это (`SystemClock` в `run_react`) или счётчик событий (тесты).
pub fn run_react_over_feed<S: OrderSigner>(
    feed: &mut dyn Feed,
    cfg: &ReactCoreConfig,
    signer: &S,
    symbol: &str,
    stage_clock: &MonotonicClock,
    mut should_stop: impl FnMut() -> bool,
) -> (Vec<ReactSample>, u64) {
    let mut state = ReactCoreState::new(cfg.tick_e9, cfg.step_e9);
    let mut samples = Vec::new();
    let mut events: u64 = 0;

    while let Some(event) = feed.next_event() {
        events += 1;
        process_event(
            &mut state,
            cfg,
            signer,
            symbol,
            stage_clock,
            event,
            &mut samples,
        );
        if should_stop() {
            break;
        }
    }
    (samples, events)
}

fn write_react_csv(path: &Path, samples: &[ReactSample]) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(path)?;
    w.write_record([
        "trigger",
        "parse_ns",
        "book_ns",
        "trigger_ns",
        "order_ns",
        "full_ns",
    ])?;
    for (i, s) in samples.iter().enumerate() {
        w.write_record([
            i.to_string(),
            s.parse_ns.map(|v| v.to_string()).unwrap_or_default(),
            s.book_ns.to_string(),
            s.trigger_ns.to_string(),
            s.order_ns.to_string(),
            s.full_ns.to_string(),
        ])?;
    }
    w.flush()?;
    Ok(())
}

fn read_probe_rtt(path: &Path) -> anyhow::Result<Option<(i64, i64)>> {
    let mut r = match csv::Reader::from_path(path) {
        Ok(r) => r,
        Err(_) => return Ok(None),
    };
    let mut rtts: Vec<i64> = Vec::new();
    for row in r.records() {
        let row = row?;
        if let Some(v) = row.get(1).and_then(|s| s.parse::<i64>().ok()) {
            rtts.push(v);
        }
    }
    if rtts.is_empty() {
        return Ok(None);
    }
    Ok(Some((
        probe::percentile_ns(&rtts, 50),
        probe::percentile_ns(&rtts, 95),
    )))
}

/// Точка входа `lob react`: живой `Feed` на один инструмент пула, ≤ 5 минут
/// (`args.minutes`, отклоняет больше `MAX_MINUTES`), настоящие ключи из
/// окружения (`Credentials::from_env`, Decision 12) — сборка и подпись
/// кадра происходят по-настоящему, только `send` заменён меткой.
pub fn run_react(args: &ReactArgs) -> anyhow::Result<ReactReport> {
    if args.minutes == 0 || args.minutes > MAX_MINUTES {
        anyhow::bail!(
            "--minutes обязан быть в 1..={MAX_MINUTES} (CLAUDE.md, R78): получено {}",
            args.minutes
        );
    }
    let side = parse_side(&args.side)?;
    let creds = Credentials::from_env()
        .map_err(|e| anyhow::anyhow!("ключи: {e:?} — выставьте BYBIT_API_KEY/BYBIT_API_SECRET"))?;
    let cfg = ReactCoreConfig {
        tick_e9: args.tick_e9,
        step_e9: args.step_e9,
        side,
        qty_e9: args.qty_e9,
        recv_window_ms: args.recv_window_ms,
    };

    let pool = vec![PoolMember {
        symbol: args.symbol.clone(),
        tick_e9: args.tick_e9,
        step_e9: args.step_e9,
    }];
    let mut feed = LiveFeed::spawn(pool);
    let stage_clock = MonotonicClock::start();
    let deadline_ns = SystemClock.now_ns()
        + i64::try_from(args.minutes.saturating_mul(60)).unwrap_or(i64::MAX) * 1_000_000_000;

    let (samples, events) =
        run_react_over_feed(&mut feed, &cfg, &creds, &args.symbol, &stage_clock, || {
            SystemClock.now_ns() >= deadline_ns
        });

    finish_report(args, samples, events)
}

fn finish_report(
    args: &ReactArgs,
    samples: Vec<ReactSample>,
    events: u64,
) -> anyhow::Result<ReactReport> {
    let triggers = samples.len() as u64;
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("react-{}.csv", args.symbol)));
    write_react_csv(&out, &samples)?;

    let gated = triggers >= MIN_TRIGGERS_FOR_GATE;
    let parse: Vec<i64> = samples.iter().filter_map(|s| s.parse_ns).collect();
    let book: Vec<i64> = samples.iter().map(|s| s.book_ns).collect();
    let trigger: Vec<i64> = samples.iter().map(|s| s.trigger_ns).collect();
    let order: Vec<i64> = samples.iter().map(|s| s.order_ns).collect();
    let full: Vec<i64> = samples.iter().map(|s| s.full_ns).collect();

    let full_summary = summarize_stage(&full);
    let g_lat_pass = if gated {
        full_summary.map(|s| s.p99_ns < G_LAT_BUDGET_NS)
    } else {
        None
    };
    let horizons = full_summary
        .map(|s| horizon_reach(s.p99_ns))
        .unwrap_or_default();

    let probe_rtt_ns = match &args.probe_csv {
        Some(p) => read_probe_rtt(p)?,
        None => None,
    };

    Ok(ReactReport {
        events,
        triggers,
        gated,
        parse: summarize_stage(&parse),
        book: summarize_stage(&book),
        trigger: summarize_stage(&trigger),
        order: summarize_stage(&order),
        full_path: full_summary,
        g_lat_pass,
        horizons,
        host_id: resolve_host_id(args.host_id.as_deref()),
        probe_rtt_ns,
        out,
    })
}

/// Печать отчёта — общий формат для `dispatch` и тестов, чтобы вывод не
/// разошёлся с тем, что реально проверяется.
pub fn format_report(rep: &ReactReport) -> String {
    let mut lines = Vec::new();
    lines.push(format!(
        "react: events={} triggers={} out={}",
        rep.events,
        rep.triggers,
        rep.out.display()
    ));
    if !rep.gated {
        lines.push(format!(
            "# lob react: debug — triggers={} < {MIN_TRIGGERS_FOR_GATE}, гейт G-LAT не объявлен",
            rep.triggers
        ));
    }
    let stage = |name: &str, s: &Option<StageSummary>| match s {
        Some(s) => format!(
            "  {name}: n={} median_ns={} p99_ns={}",
            s.n, s.median_ns, s.p99_ns
        ),
        None => format!("  {name}: нет данных"),
    };
    lines.push(stage("разбор", &rep.parse));
    lines.push(stage("книга", &rep.book));
    lines.push(stage("триггер", &rep.trigger));
    lines.push(stage("ордер(send)", &rep.order));
    lines.push(stage("весь путь", &rep.full_path));
    match rep.g_lat_pass {
        Some(true) => lines.push(format!(
            "G-LAT: PASS (p99 < {} мс)",
            G_LAT_BUDGET_NS / 1_000_000
        )),
        Some(false) => lines.push(format!(
            "G-LAT: FAIL (p99 >= {} мс)",
            G_LAT_BUDGET_NS / 1_000_000
        )),
        None => lines.push("G-LAT: не объявлен (недостаточно срабатываний)".to_string()),
    }
    for h in &rep.horizons {
        lines.push(format!(
            "horizon {}ms: {}",
            h.horizon_ms,
            if h.reachable {
                "reachable"
            } else {
                "unreachable"
            }
        ));
    }
    lines.push(format!(
        "host={} probe_rtt={}",
        rep.host_id,
        match rep.probe_rtt_ns {
            Some((median, p95)) => format!("median_ns={median} p95_ns={p95}"),
            None => "не измерен (нет --probe-csv)".to_string(),
        }
    ));
    lines.join("\n")
}

// ---------------------------------------------------------------------------
// Грепом по образцу `levels.rs`: горячий путь не вправе звать часы напрямую.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod hot_path_guard {
    #[test]
    fn module_never_calls_the_wall_clock_directly_in_the_measured_loop() {
        const SRC: &str = include_str!("react.rs");
        let banned = [concat!("Inst", "ant::now"), concat!("System", "Time::now")];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    struct ScriptedFeed {
        events: VecDeque<Event>,
    }

    impl Feed for ScriptedFeed {
        fn next_event(&mut self) -> Option<Event> {
            self.events.pop_front()
        }
    }

    /// `u` — счётчик последовательности `Book::apply` (не связан с `Feed`,
    /// который в этом файле его не видит): снапшот несёт `u=1`, каждая
    /// следующая дельта — `u` на единицу больше предыдущей, иначе
    /// `Book::apply` честно откажет `SequenceGap`, и тест не увидит ничего.
    ///
    /// Дельта, а не снапшот: старый уровень каждой стороны обязан быть
    /// явно обнулён (`(old, 0)`), иначе он остаётся в книге навсегда и при
    /// движении цены вверх старый аск оказывается ниже нового бида —
    /// `Crossed`, а не смена тика (реальный Bybit-делта устроен так же: не
    /// названный явно уровень не исчезает сам). Когда `old == new`, пара
    /// `(px, 0)` затем `(px, qty)` — недействующий ноль, тик остаётся тем же.
    fn book_event(
        local_ts_ns: i64,
        parse_ns: Option<i64>,
        u: u64,
        old_bid_e9: i64,
        new_bid_e9: i64,
        old_ask_e9: i64,
        new_ask_e9: i64,
    ) -> Event {
        use crate::book::Update;
        Event::Market {
            symbol: 0,
            local_ts_ns,
            parse_latency_ns: parse_ns,
            payload: crate::bybit::ws::Event::Book(Update {
                is_snapshot: false,
                u,
                cts_ms: local_ts_ns / 1_000_000,
                seq: 0,
                bids: vec![(old_bid_e9, 0), (new_bid_e9, 1_000_000_000)],
                asks: vec![(old_ask_e9, 0), (new_ask_e9, 1_000_000_000)],
            }),
        }
    }

    fn snapshot_event(local_ts_ns: i64, bid_e9: i64, ask_e9: i64) -> Event {
        use crate::book::Update;
        Event::Market {
            symbol: 0,
            local_ts_ns,
            parse_latency_ns: Some(1_000),
            payload: crate::bybit::ws::Event::Book(Update {
                is_snapshot: true,
                u: 1,
                cts_ms: local_ts_ns / 1_000_000,
                seq: 0,
                bids: vec![(bid_e9, 1_000_000_000)],
                asks: vec![(ask_e9, 1_000_000_000)],
            }),
        }
    }

    fn test_creds() -> Credentials {
        Credentials::for_test("test-key", "test-secret")
    }

    fn cfg() -> ReactCoreConfig {
        ReactCoreConfig {
            tick_e9: 10_000_000,
            step_e9: 1_000_000,
            side: OrderSide::Buy,
            qty_e9: 1_000_000,
            recv_window_ms: 5_000,
        }
    }

    /// Снапшот сам по себе не двигает лучший тик (первое наблюдение), а
    /// каждое следующее реальное изменение цены — срабатывание: три смены
    /// подряд после снапшота обязаны дать три образца, не четыре и не два.
    #[test]
    fn each_top_of_book_change_after_the_first_is_a_trigger() {
        let px = |n: i64| n * 10_000_000;
        let events = VecDeque::from(vec![
            snapshot_event(0, px(100), px(101)),
            book_event(
                1_000_000,
                Some(150_000),
                2,
                px(100),
                px(100),
                px(101),
                px(101),
            ), // тот же тик — не триггер
            book_event(
                2_000_000,
                Some(160_000),
                3,
                px(100),
                px(101),
                px(101),
                px(102),
            ), // сдвиг — триггер 1
            book_event(
                3_000_000,
                Some(170_000),
                4,
                px(101),
                px(102),
                px(102),
                px(103),
            ), // сдвиг — триггер 2
        ]);
        let mut feed = ScriptedFeed { events };
        let clock = MonotonicClock::start();
        let creds = test_creds();
        let (samples, processed) =
            run_react_over_feed(&mut feed, &cfg(), &creds, "SOLUSDT", &clock, || false);

        assert_eq!(processed, 4, "все четыре события обязаны быть прочитаны");
        assert_eq!(
            samples.len(),
            2,
            "срабатывания только на реальную смену тика: {samples:?}"
        );
        for s in &samples {
            assert!(s.book_ns >= 0 && s.trigger_ns >= 0 && s.order_ns >= 0 && s.full_ns >= 0);
        }
        assert_eq!(
            samples[0].parse_ns,
            Some(160_000),
            "срабатывание 1 — кадр в 2с"
        );
        assert_eq!(
            samples[1].parse_ns,
            Some(170_000),
            "срабатывание 2 — кадр в 3с"
        );
    }

    /// Первое срабатывание использует ордер, подготовленный на предыдущем
    /// обновлении (снапшоте) — то есть уже ПЕРВАЯ смена тика после снапшота
    /// даёт образец, а не вторая (иначе «готов заранее» не выполняется).
    #[test]
    fn the_very_first_change_after_the_snapshot_is_already_a_trigger() {
        let px = |n: i64| n * 10_000_000;
        let events = VecDeque::from(vec![
            snapshot_event(0, px(100), px(101)),
            book_event(
                1_000_000,
                Some(150_000),
                2,
                px(100),
                px(101),
                px(101),
                px(102),
            ),
        ]);
        let mut feed = ScriptedFeed { events };
        let clock = MonotonicClock::start();
        let creds = test_creds();
        let (samples, _) =
            run_react_over_feed(&mut feed, &cfg(), &creds, "SOLUSDT", &clock, || false);
        assert_eq!(samples.len(), 1, "готовность уже на снапшоте: {samples:?}");
        assert_eq!(samples[0].parse_ns, Some(150_000));
    }

    /// `should_stop` останавливает чтение немедленно — ядро не эксплуатирует
    /// весь `Feed`, если вызывающий решил хватит (живой дедлайн в
    /// `run_react`).
    #[test]
    fn should_stop_ends_the_loop_without_draining_the_feed() {
        let px = |n: i64| n * 10_000_000;
        let events = VecDeque::from(vec![
            snapshot_event(0, px(100), px(101)),
            book_event(1_000_000, Some(1), 2, px(100), px(101), px(101), px(102)),
            book_event(2_000_000, Some(1), 3, px(101), px(102), px(102), px(103)),
        ]);
        let mut feed = ScriptedFeed { events };
        let clock = MonotonicClock::start();
        let creds = test_creds();
        let mut calls = 0;
        let (_samples, processed) =
            run_react_over_feed(&mut feed, &cfg(), &creds, "SOLUSDT", &clock, || {
                calls += 1;
                calls >= 1
            });
        assert_eq!(processed, 1, "остановка после первого события: {processed}");
    }

    /// Фейк-подписант без единой аллокации — тот же приём, что
    /// `bybit::trade_ws::tests::ZeroAllocSigner`, своя копия здесь: та
    /// приватна тестовому модулю другого файла (шов «`Transport`/подпись —
    /// фейк», не обязательно один экземпляр на весь репозиторий).
    struct ZeroAllocSigner {
        api_key: String,
    }

    impl OrderSigner for ZeroAllocSigner {
        fn sign(
            &self,
            _timestamp_ms: i64,
            _recv_window_ms: u32,
            _body: &str,
        ) -> Result<String, crate::bybit::sign::CredentialsError> {
            unreachable!("тест зовёт только sign_into")
        }
        fn api_key(&self) -> &str {
            &self.api_key
        }
        fn sign_into(
            &self,
            _timestamp_ms: i64,
            _recv_window_ms: u32,
            _body: &str,
            out: &mut [u8; 64],
        ) -> Result<(), crate::bybit::sign::CredentialsError> {
            out.fill(b'a');
            Ok(())
        }
    }

    /// Запрет 1 горячего пути (дозапрос ревью таска 15): 10⁶ событий через
    /// `process_event` (шаг цикла `run_react_over_feed`) на одном и том же
    /// `ReactCoreState` — цена своей стороны спреда меняется каждое
    /// `PRICE_CHANGE_EVERY`-е событие, то есть `rebuild` реально вызывается
    /// десятки тысяч раз внутри измеряемого окна, не только на прогреве. И
    /// путь «разбор → книга → триггер» на неизменной цене, и сам `rebuild`
    /// на изменившейся — обязаны не аллоцировать после прогрева.
    #[test]
    fn process_event_allocates_nothing_per_event_after_warmup() {
        const WARMUP: usize = 2_000;
        const MEASURED: usize = 1_000_000;
        const PRICE_CHANGE_EVERY: i64 = 97;
        // Цикл через небольшой набор уровней — цифры числа не растут в
        // течение теста, иначе рост ёмкости строковых буферов внутри
        // измеряемого окна был бы артефактом сценария, а не находкой.
        const LEVEL_PERIOD: i64 = 5;

        let px = |n: i64| n * 10_000_000;
        let make_event = |i: usize| -> Event {
            let level = 100 + (i as i64 / PRICE_CHANGE_EVERY) % LEVEL_PERIOD;
            if i == 0 {
                return snapshot_event(0, px(level), px(level + 1));
            }
            let prev_level = 100 + ((i as i64 - 1) / PRICE_CHANGE_EVERY) % LEVEL_PERIOD;
            book_event(
                i as i64 * 1_000,
                Some(1_000),
                (i + 1) as u64,
                px(prev_level),
                px(level),
                px(prev_level + 1),
                px(level + 1),
            )
        };

        let signer = ZeroAllocSigner {
            api_key: "test-key".to_string(),
        };
        let cfg = cfg();
        let clock = MonotonicClock::start();
        let mut state = ReactCoreState::new(cfg.tick_e9, cfg.step_e9);
        // Ёмкость с запасом заранее: иначе рост `Vec` из-за собственных
        // срабатываний теста реаллоцировал бы внутри измеряемого окна и
        // проверял бы сценарий, а не `process_event` (та же ловушка, что
        // `ReadyMakerOrder`'s буферы решают через `with_capacity`).
        let mut samples = Vec::with_capacity(20_000);

        for i in 0..WARMUP {
            process_event(
                &mut state,
                &cfg,
                &signer,
                "SOLUSDT",
                &clock,
                make_event(i),
                &mut samples,
            );
        }

        let mut total_allocations = 0u64;
        for i in WARMUP..(WARMUP + MEASURED) {
            let event = make_event(i);
            let (_, counts) = crate::alloc_count::measure(|| {
                process_event(
                    &mut state,
                    &cfg,
                    &signer,
                    "SOLUSDT",
                    &clock,
                    event,
                    &mut samples,
                )
            });
            total_allocations += counts.allocations;
        }
        assert!(
            samples.len() > MIN_TRIGGERS_FOR_GATE as usize,
            "сценарий обязан дать много срабатываний внутри измеряемого окна: {}",
            samples.len()
        );
        assert_eq!(
            total_allocations, 0,
            "process_event аллоцировал после прогрева — запрет 1 interfaces.md"
        );
    }

    /// Пустая выборка не печатает выдуманных чисел (правило 1
    /// `interfaces.md`) — `summarize_stage`/`format_report` честно говорят
    /// «нет данных», гейт G-LAT не объявляется на выборке меньше 1000.
    #[test]
    fn a_short_run_reports_debug_and_declares_no_gate() {
        let args = ReactArgs {
            symbol: "SOLUSDT".to_string(),
            tick_e9: 10_000_000,
            step_e9: 1_000_000,
            side: "buy".to_string(),
            qty_e9: 1_000_000,
            recv_window_ms: 5_000,
            minutes: 5,
            root: std::env::temp_dir(),
            out: Some(std::env::temp_dir().join("react-test-empty.csv")),
            host_id: Some("test-host".to_string()),
            probe_csv: None,
        };
        let rep = finish_report(&args, Vec::new(), 0).unwrap();
        assert!(!rep.gated);
        assert_eq!(rep.g_lat_pass, None);
        assert!(rep.horizons.is_empty());
        let printed = format_report(&rep);
        assert!(printed.contains("debug"));
        assert!(printed.contains("не объявлен"));
    }

    /// Гейт объявляется только на ≥ 1000 срабатываниях, и p99 полного пути
    /// решает PASS/FAIL буквально по `G_LAT_BUDGET_NS`.
    #[test]
    fn gate_declared_and_passes_when_p99_is_under_budget_with_enough_triggers() {
        let samples: Vec<ReactSample> = (0..MIN_TRIGGERS_FOR_GATE)
            .map(|_| ReactSample {
                parse_ns: Some(50_000),
                book_ns: 10_000,
                trigger_ns: 5_000,
                order_ns: 5_000,
                full_ns: 1_000_000, // 1мс — под бюджетом 5мс
            })
            .collect();
        let args = ReactArgs {
            symbol: "SOLUSDT".to_string(),
            tick_e9: 10_000_000,
            step_e9: 1_000_000,
            side: "buy".to_string(),
            qty_e9: 1_000_000,
            recv_window_ms: 5_000,
            minutes: 5,
            root: std::env::temp_dir(),
            out: Some(std::env::temp_dir().join("react-test-full.csv")),
            host_id: None,
            probe_csv: None,
        };
        let rep = finish_report(&args, samples, MIN_TRIGGERS_FOR_GATE).unwrap();
        assert!(rep.gated);
        assert_eq!(rep.g_lat_pass, Some(true));
        // 1мс — < 20% от каждого из четырёх горизонтов (100мс..60с) — все достижимы.
        assert!(
            rep.horizons.iter().all(|h| h.reachable),
            "{:?}",
            rep.horizons
        );
        assert!(rep.host_id == "unknown-host" || !rep.host_id.is_empty());
    }

    /// D-HOR буквально: `p99` больше 20% длины горизонта — `unreachable`, и
    /// это не валит гейт G-LAT (тот смотрит только на общий бюджет 5мс).
    #[test]
    fn a_horizon_shorter_than_five_times_p99_is_marked_unreachable() {
        // p99 = 30мс: 20% от 100мс = 20мс -> недостижим; 20% от 1с = 200мс -> достижим.
        let samples: Vec<ReactSample> = (0..MIN_TRIGGERS_FOR_GATE)
            .map(|_| ReactSample {
                parse_ns: Some(50_000),
                book_ns: 10_000,
                trigger_ns: 5_000,
                order_ns: 5_000,
                full_ns: 30_000_000,
            })
            .collect();
        let args = ReactArgs {
            symbol: "SOLUSDT".to_string(),
            tick_e9: 10_000_000,
            step_e9: 1_000_000,
            side: "buy".to_string(),
            qty_e9: 1_000_000,
            recv_window_ms: 5_000,
            minutes: 5,
            root: std::env::temp_dir(),
            out: Some(std::env::temp_dir().join("react-test-hor.csv")),
            host_id: None,
            probe_csv: None,
        };
        let rep = finish_report(&args, samples, MIN_TRIGGERS_FOR_GATE).unwrap();
        let h100 = rep.horizons.iter().find(|h| h.horizon_ms == 100).unwrap();
        let h1000 = rep.horizons.iter().find(|h| h.horizon_ms == 1_000).unwrap();
        assert!(!h100.reachable, "{h100:?}");
        assert!(h1000.reachable, "{h1000:?}");
    }

    /// `--minutes` за пределами `1..=MAX_MINUTES` отклоняется до сети (нет
    /// ключей, нет сокета — просто разбор аргументов и граница).
    #[test]
    fn minutes_outside_the_debug_ceiling_is_rejected_before_touching_the_network() {
        let args = ReactArgs {
            symbol: "SOLUSDT".to_string(),
            tick_e9: 10_000_000,
            step_e9: 1_000_000,
            side: "buy".to_string(),
            qty_e9: 1_000_000,
            recv_window_ms: 5_000,
            minutes: MAX_MINUTES + 1,
            root: std::env::temp_dir(),
            out: None,
            host_id: None,
            probe_csv: None,
        };
        assert!(run_react(&args).is_err());
    }
}
