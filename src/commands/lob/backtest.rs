//! `lob backtest` — CLI-обёртка вокруг движка `lob::backtest` (таск 11,
//! спека — истории 32–34, §6). Единственная зона, которой разрешено знать
//! одновременно про `Feed`/`bybit::ws` (площадка) и про `hftbacktest::types::
//! Event` (нейтральный формат крейта) — граница «чистое ядро стратегии не
//! тянет транспорт и площадку» держится тестом-грепом в `lob/backtest.rs`,
//! а перевод одного формата в другой поэтому живёт здесь, а не там.
//!
//! Вход — `ReplayFeed` по бинлогу одной сессии одного символа (таск 19:
//! `<SYMBOL>-<день>.binlog`, резолвер `super::session_binlog_for`;
//! `data/session-debug/<ts>/…`, `data/recon5/<SYMBOL>/…` — отладочные,
//! метка `debug`, `CLAUDE.md`). Сигналы (какие уровни торговать
//! и какой стороной) приходят отдельным CSV — этот файл их не размечает и
//! не классифицирует по осям семи профилей (это `lob levels`/будущая
//! проводка таска 12); здесь только: `profile_id,side,birth_ms` — сторона
//! `bid`/`ask` (Decision 14) и момент срабатывания в мс, тот же смысл, что
//! колонка `birth_ms` артефакта `lob levels`.

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog;
use crate::book::Side;
use crate::bybit::ws::Event as WsEvent;
use crate::feed::{replay::ReplayFeed, Event as FeedEvent, Feed};
use crate::lob::backtest::{
    build_backtest, build_profile_report, drive_bounce, drive_profile, mean_net_bps, pnl_curve_bps,
    BacktestReport, BounceRun, BounceSignal, DriveConfig, Signal, TableEstimate, SIGMA_LONG,
    SIGMA_SHORT,
};
use crate::lob::costs::{net_fill_bps, net_fill_interval};
use crate::lob::levels::{H3Mode, LevelRecord, LevelsConfig, TouchRecord};
use crate::lob::markout::{MidSample, HORIZONS_MS};
use crate::lob::strategy::TradePlan;
use crate::lob::touch_axes::{
    age_bucket, frontrun_bucket, frontrun_share, round_bucket, touch_index_bucket,
};
use hftbacktest::types::{
    Event as HbtEvent, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT,
    EXCH_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BUY_TRADE_EVENT, LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

use super::profiles::{size_bucket, FillModel};
use super::side_name;

// ---------------------------------------------------------------------------
// `lob backtest` (шаг 6.3, история 32–34).
// ---------------------------------------------------------------------------

/// Аргументы `lob backtest`: вердикт бэктеста с моделью очереди на
/// произвольное число профилей.
#[derive(Debug, Args)]
pub struct BacktestArgs {
    /// Корень сессии: файл `<SYMBOL>-<день>.binlog` (см. `lob session`,
    /// резолвер `super::session_binlog_for`).
    #[arg(long)]
    pub session_root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// CSV сигналов: `profile_id,side,birth_ms` (`side`: `bid`|`ask` —
    /// Decision 14; `birth_ms` — момент срабатывания, тот же смысл, что
    /// колонка `birth_ms` артефакта `lob levels`). Несколько строк одного
    /// `profile_id` — один профиль. Не нужен с `--touches`.
    #[arg(long)]
    pub signals_csv: Option<PathBuf>,
    /// Медианная замеренная RTT исполнения, нс (D-RTT: источник — `lob
    /// probe`/`clock.csv`/`probe-*.csv`). Без умолчания: изобретённое число
    /// запрещено (§9 плана).
    #[arg(long)]
    pub median_rtt_ns: i64,
    /// 95-й перцентиль той же замеренной RTT.
    #[arg(long)]
    pub p95_rtt_ns: i64,
    /// Размер круга в 1e-9 лотов — минимальный лот площадки (Decision 22:
    /// `order_size_22a` из `instruments.csv`, не шаг книги). Без умолчания
    /// нарочно: шаг книги (`step_e9` бинлога) — другая величина, и молчаливо
    /// подставлять его вместо лота площадки было бы неверным умолчанием, а
    /// не измеренным (§9 плана).
    #[arg(long)]
    pub order_qty_e9: i64,
    /// Таблица профилей (таск 10, `docs/findings/profiles-<дата>.csv`) для
    /// сравнения `net_fill`. Без флага сравнение печатает `none`. Строки, где
    /// `fill_model=none` (колонки `fill`/`net_fill` — литерал `not_measured`),
    /// сравниваются по `net_bps` — см. `TableComparison`.
    #[arg(long)]
    pub profiles_csv: Option<PathBuf>,
    /// Куда писать сводку (по умолчанию `docs/findings/backtest-<дата>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Куда писать кривые PnL по обеим RTT (по умолчанию
    /// `docs/findings/backtest-<дата>-pnl.csv`).
    #[arg(long)]
    pub pnl_out: Option<PathBuf>,
    /// Вход — отладочная запись (`data/session-debug/…`, короче боевого
    /// окна): метит оба артефакта строкой `# lob backtest: …` с суффиксом
    /// ` debug`, тем же приёмом, что `lob profiles --allow-unverified`.
    /// Решение вызывающего, не автоопределение — как там же.
    #[arg(long, default_value_t = false)]
    pub debug: bool,
    /// Вместо CSV сигналов — **касания живых уровней** (В-44) из реплея того же
    /// каталога: каждая строка `touches` становится сделкой-отскоком, план
    /// которой строится здесь (бид `P`: вход `P+1` тик, стоп `P−1`, тейк
    /// `P+3`; аск зеркально), а строки CSV — оси В-44. Порог `H3` — тот же
    /// резолвер, что у `lob touches`.
    #[arg(long, default_value_t = false)]
    pub touches: bool,
    /// Вход пост-онли (`GTX`) вместо обычного лимита (`GTC`): замер того,
    /// сколько входов пересекает спред в момент касания (T38).
    #[arg(long, default_value_t = false)]
    pub post_only: bool,
    /// Трейл-тейк: откат от лучшего исхода, bps (0 — выключен, работает
    /// фиксированный тейк 1:1). Решение владельца 2026-09-13.
    #[arg(long, default_value_t = 0.0)]
    pub trail_bps: f64,
    /// Прибыль от входа, после которой трейл включается, bps.
    #[arg(long, default_value_t = 0.0)]
    pub trail_activate_bps: f64,
    /// Порог `H3` для `--touches` — те же флаги, что у `lob touches`/`levels`.
    #[command(flatten)]
    pub h3: super::H3Args,
    /// Относительный порог `floor(k × median_trade_lots)` (В-30) для `--touches`.
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Прогрев разметки, мс (`--touches`; по умолчанию `DEFAULT_WARMUP_MS`).
    #[arg(long)]
    pub warmup_ms: Option<i64>,
    /// Окно `repeat_count`, мс (`--touches`; по умолчанию `DEFAULT_REPEAT_WINDOW_MS`).
    #[arg(long)]
    pub repeat_window_ms: Option<i64>,
}

/// Итог `lob backtest` для печати диспетчером.
pub struct BacktestSummary {
    pub profiles: usize,
    pub pass: usize,
    pub red: usize,
    pub out: PathBuf,
    pub pnl_out: PathBuf,
}

/// Прогоняет бэктест на всех профилях `signals_csv` и пишет оба артефакта.
pub fn run_backtest(args: &BacktestArgs) -> anyhow::Result<BacktestSummary> {
    let binlog_paths = super::session_binlog_for(&args.session_root, &args.symbol)?;
    let (tick_e9, step_e9) = read_tick_step(&binlog_paths[0])?;
    let tick_size = tick_e9 as f64 / 1e9;
    let lot_size = step_e9 as f64 / 1e9;
    let order_qty = args.order_qty_e9 as f64 / 1e9;

    // Таск 22: сессия может нести несколько частей — читаются подряд как
    // один поток (`events_from_paths`), не только первая.
    let events = events_from_paths(&binlog_paths)?;
    if events.is_empty() {
        let names = binlog_paths
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(", ");
        anyhow::bail!("бинлог(и) {names} пусты или не разобрались");
    }

    // Сделка-отскока (В-44, таск 38): сигналы приходят не из CSV, а из
    // касаний живых уровней того же реплея — отдельная ветка, потому что и
    // план у каждой сделки свой, и строки отчёта — оси В-44.
    if args.touches {
        return run_bounce(args, &binlog_paths, tick_e9, step_e9, &events);
    }
    let Some(signals_csv) = args.signals_csv.as_deref() else {
        anyhow::bail!("нужен либо --signals <csv>, либо --touches");
    };
    let signals = read_signals(signals_csv)?;
    if signals.is_empty() {
        anyhow::bail!("сигналов нет: {}", signals_csv.display());
    }
    let table = read_table(args.profiles_csv.as_deref())?;

    let cfg = DriveConfig {
        order_qty,
        first_order_id: 1,
    };

    let mut reports = Vec::with_capacity(signals.len());
    for (profile_id, sigs) in &signals {
        let mut median_bt = build_backtest(&events, tick_size, lot_size, args.median_rtt_ns);
        let median = drive_profile(&mut median_bt, 0, sigs, &cfg)?;
        let mut p95_bt = build_backtest(&events, tick_size, lot_size, args.p95_rtt_ns);
        let p95 = drive_profile(&mut p95_bt, 0, sigs, &cfg)?;
        let estimate = table.get(profile_id).copied();
        reports.push(build_profile_report(
            profile_id.clone(),
            median,
            p95,
            estimate,
        ));
    }
    let report = BacktestReport::new(reports);

    let date = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/backtest-{date}.csv")));
    let pnl_out = args
        .pnl_out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/backtest-{date}-pnl.csv")));

    let header = header_comment(args);
    write_backtest_csv(&out, &report, &header)?;
    write_pnl_csv(&pnl_out, &report, &header)?;

    for line in report.summary_lines() {
        println!("{line}");
    }

    let pass = report.profiles.iter().filter(|p| p.g4.is_pass()).count();
    let red = report.profiles.len() - pass;
    Ok(BacktestSummary {
        profiles: report.profiles.len(),
        pass,
        red,
        out,
        pnl_out,
    })
}

// ---------------------------------------------------------------------------
// Бинлог: заголовок (тик/лот) и `ReplayFeed`.
// ---------------------------------------------------------------------------

fn read_tick_step(path: &Path) -> anyhow::Result<(i64, i64)> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
    let reader = binlog::Reader::open(file)
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    Ok((header.tick_e9, header.step_e9))
}

fn open_replay_feed(path: &Path) -> anyhow::Result<ReplayFeed<std::fs::File>> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
    ReplayFeed::open(0, file).map_err(|e| anyhow::anyhow!("бинлог {}: {e:?}", path.display()))
}

/// События крейта по всем частям сессии подряд (таск 22: `session_binlog_for`
/// отдаёт список, не один файл) — конкатенация, не второй `Feed`: `feed/`
/// вне зоны этого таска, а `ReplayFeed` не читает несколько файлов сам
/// (каждый несёт собственный заголовок), поэтому склейка на уровне уже
/// переведённых событий, здесь, а не в `feed::replay`.
fn events_from_paths(paths: &[PathBuf]) -> anyhow::Result<Vec<HbtEvent>> {
    let mut events = Vec::new();
    for path in paths {
        let mut feed = open_replay_feed(path)?;
        events.extend(events_from_feed(&mut feed));
    }
    Ok(events)
}

// ---------------------------------------------------------------------------
// `BacktestFillModel` (таск 16) — `profiles::FillModel` поверх `lob::backtest`:
// соединяет таблицу профилей/шорт-лист с настоящей моделью очереди
// (`RiskAdverseQueueModel`), закрывая BLOCKERS таска 13 («требует реального
// книжного потока сессии, не только `mids`»).
// ---------------------------------------------------------------------------

/// σ по стороне книги (Decision 14) — тот же выбор, что `read_signals` делает
/// из строки `bid`/`ask` `signals_csv`.
fn sigma_of(side: Side) -> i8 {
    match side {
        Side::Bid => SIGMA_SHORT,
        Side::Ask => SIGMA_LONG,
    }
}

/// Естественный ключ уровня внутри одного символа (`interfaces.md`:
/// `LevelRecord` — «рождение, сторона, цена»). `birth_ms` — эпоховые мс
/// (`up.cts_ms` источника, не относительное время сессии), поэтому пары
/// разных сессий одного символа не сталкиваются: кэш общий на всю модель, не
/// per-сессия.
type LevelKey = (String, i8, i64, i64);

fn level_key(symbol: &str, rec: &LevelRecord) -> LevelKey {
    (
        symbol.to_string(),
        sigma_of(rec.side),
        rec.price_tick,
        rec.birth_ms,
    )
}

/// `FillModel` (`commands::lob::profiles`) поверх `lob::backtest`: гоняет
/// `strategy::on_event` через настоящую очередь `RiskAdverseQueueModel` на
/// книжном потоке сессии (не только `mids`) и отвечает `filled` по кэшу,
/// заполненному `prime_session` — один прогон движка на сессию
/// (`interfaces.md`, BLOCKERS таска 13), не на уровень.
///
/// Сигналы этого прогона — **все** размеченные уровни сессии в порядке
/// таймлайна, а не только уровни одного профиля: одна позиция за раз
/// (`drive_profile`) моделируется на настоящем потоке сигналов стратегии, а
/// не на подмножестве, отфильтрованном по оси профиля — иначе два разных
/// профиля (например `marginal:side=bid` и `marginal:size=…`), которым
/// принадлежит один и тот же уровень, увидели бы разные занятости позиции по
/// одному и тому же событию.
///
/// RTT — median/p95 (те же обязательные параметры, что `lob backtest`, без
/// умолчания, §9). `filled()` решает по медианной: тот же выбор, что уже
/// сделан `TableComparison`/`compare_with_table` («по медианной — основной
/// сценарий»). `p95_rtt_ns` сохранён для симметрии CLI и возможного будущего
/// потребителя (`p95_rtt_ns()`) — сегодня решение `filled()` не меняет.
#[derive(Debug)]
pub struct BacktestFillModel {
    median_rtt_ns: i64,
    p95_rtt_ns: i64,
    order_qty_e9: i64,
    cache: RefCell<BTreeMap<LevelKey, bool>>,
}

impl BacktestFillModel {
    pub fn new(median_rtt_ns: i64, p95_rtt_ns: i64, order_qty_e9: i64) -> Self {
        Self {
            median_rtt_ns,
            p95_rtt_ns,
            order_qty_e9,
            cache: RefCell::new(BTreeMap::new()),
        }
    }

    /// 95-й перцентиль RTT, задан вместе с медианной (см. doc структуры).
    pub fn p95_rtt_ns(&self) -> i64 {
        self.p95_rtt_ns
    }

    /// Прогоняет движок один раз на уже переведённый в события крейта поток
    /// (тестовый шов: `prime_session` собирает `events`/`tick`/`lot` из
    /// файла и зовёт этот метод — юнит-тест кормит синтетику напрямую, без
    /// файла бинлога).
    fn prime_from_events(
        &self,
        symbol: &str,
        events: &[HbtEvent],
        tick_size: f64,
        lot_size: f64,
        records: &[LevelRecord],
    ) {
        if events.is_empty() || records.is_empty() {
            return;
        }
        let order_qty = self.order_qty_e9 as f64 / 1e9;
        let cfg = DriveConfig {
            order_qty,
            first_order_id: 1,
        };
        let signals: Vec<Signal> = records
            .iter()
            .map(|r| Signal {
                t0_ns: r.birth_ms.saturating_mul(1_000_000),
                sigma: sigma_of(r.side),
            })
            .collect();
        let mut bt = build_backtest(events, tick_size, lot_size, self.median_rtt_ns);
        let Ok(run) = drive_profile(&mut bt, 0, &signals, &cfg) else {
            return;
        };
        // `drive_profile` сортирует сигналы по `t0_ns` стабильно и кладёт
        // ровно одну `FillObservation` на сигнал из этого порядка, пока не
        // упрётся в конец данных (`incomplete`) — тогда хвост остаётся без
        // наблюдения. Тот же стабильный порядок на исходных индексах
        // восстанавливает, какая запись какому наблюдению отвечает, без
        // повторного прогона движка (см. doc структуры).
        let mut order_idx: Vec<usize> = (0..records.len()).collect();
        order_idx.sort_by_key(|&i| signals[i].t0_ns);

        let mut cache = self.cache.borrow_mut();
        for (k, obs) in run.observations.iter().enumerate() {
            let Some(&orig) = order_idx.get(k) else {
                break;
            };
            let rec = &records[orig];
            cache.insert(level_key(symbol, rec), obs.filled);
        }
    }
}

impl FillModel for BacktestFillModel {
    /// Таск 22: сессия может нести несколько частей (`session_binlog_for`) —
    /// тик/лот берутся из заголовка первой части (части одной сессии не
    /// меняют шаги, в отличие от ротации `lob record` по смене шагов), а
    /// событийный поток — конкатенация всех частей по порядку
    /// (`events_from_paths`), один прогон движка на всю сессию, как раньше
    /// на один файл.
    fn prime_session(&self, symbol: &str, binlog_paths: &[PathBuf], records: &[LevelRecord]) {
        if records.is_empty() {
            return;
        }
        let Some(first) = binlog_paths.first() else {
            return;
        };
        let Ok((tick_e9, step_e9)) = read_tick_step(first) else {
            return;
        };
        let Ok(events) = events_from_paths(binlog_paths) else {
            return;
        };
        self.prime_from_events(
            symbol,
            &events,
            tick_e9 as f64 / 1e9,
            step_e9 as f64 / 1e9,
            records,
        );
    }

    fn filled(&self, symbol: &str, rec: &LevelRecord, _mids: &[MidSample]) -> Option<bool> {
        self.cache.borrow().get(&level_key(symbol, rec)).copied()
    }

    fn label(&self) -> &'static str {
        "backtest"
    }
}

// ---------------------------------------------------------------------------
// `Feed` (снапшоты/дельты стакана, сделки) → события `hftbacktest`. Цена и
// размер уже в 1e-9 у источника (`book::Update`/`bybit::ws::Trade`) — делить
// на 1e9 единственный раз, на границе `MarketDepth` (A7).
// ---------------------------------------------------------------------------

/// Переводит поток `Feed` в события крейта для `L2AssetBuilder`/
/// `Data::from_data`. `is_snapshot` требует явно обнулить уровни, которых
/// нет в новом снапшоте (то же правило, что `book::Book::apply` — очистка
/// перед применением): функция ведёт свой минимальный учёт видимых цен по
/// стороне только для этого обнуления — это перевод в события, не книга.
fn events_from_feed(feed: &mut dyn Feed) -> Vec<HbtEvent> {
    let mut out = Vec::new();
    let mut known_bids: BTreeMap<i64, i64> = BTreeMap::new();
    let mut known_asks: BTreeMap<i64, i64> = BTreeMap::new();

    while let Some(ev) = feed.next_event() {
        let FeedEvent::Market {
            local_ts_ns,
            payload,
            ..
        } = ev
        else {
            continue;
        };
        match payload {
            WsEvent::Book(up) => {
                let exch_ts = up.cts_ms.saturating_mul(1_000_000);
                push_side(
                    &mut out,
                    &mut known_bids,
                    &up.bids,
                    up.is_snapshot,
                    exch_ts,
                    local_ts_ns,
                    true,
                );
                push_side(
                    &mut out,
                    &mut known_asks,
                    &up.asks,
                    up.is_snapshot,
                    exch_ts,
                    local_ts_ns,
                    false,
                );
            }
            WsEvent::Trade(t) => {
                let ev_bits = (if t.aggressor_is_buy {
                    LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
                } else {
                    LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
                }) | EXCH_EVENT
                    | LOCAL_EVENT;
                out.push(HbtEvent {
                    ev: ev_bits,
                    exch_ts: t.exch_ms.saturating_mul(1_000_000),
                    local_ts: local_ts_ns,
                    px: t.price_e9 as f64 / 1e9,
                    qty: t.qty_e9 as f64 / 1e9,
                    order_id: 0,
                    ival: 0,
                    fval: 0.0,
                });
            }
            WsEvent::Other => {}
        }
    }
    out
}

#[allow(clippy::too_many_arguments)]
fn push_side(
    out: &mut Vec<HbtEvent>,
    known: &mut BTreeMap<i64, i64>,
    rows: &[(i64, i64)],
    is_snapshot: bool,
    exch_ts: i64,
    local_ts: i64,
    bid: bool,
) {
    let ev_bits = if bid {
        LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
    } else {
        LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
    };
    let mut push = |px: i64, qty: i64| {
        out.push(HbtEvent {
            ev: ev_bits,
            exch_ts,
            local_ts,
            px: px as f64 / 1e9,
            qty: qty as f64 / 1e9,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        });
    };
    if is_snapshot {
        let fresh: BTreeSet<i64> = rows.iter().map(|&(px, _)| px).collect();
        let stale: Vec<i64> = known
            .keys()
            .copied()
            .filter(|px| !fresh.contains(px))
            .collect();
        for px in stale {
            known.remove(&px);
            push(px, 0);
        }
    }
    for &(px, qty) in rows {
        if qty > 0 {
            known.insert(px, qty);
        } else {
            known.remove(&px);
        }
        push(px, qty);
    }
}

// ---------------------------------------------------------------------------
// CSV сигналов и таблицы профилей.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Deserialize)]
struct SignalRow {
    profile_id: String,
    side: String,
    birth_ms: i64,
}

fn read_signals(path: &Path) -> anyhow::Result<BTreeMap<String, Vec<Signal>>> {
    let mut r = csv::Reader::from_path(path)
        .map_err(|e| anyhow::anyhow!("сигналы {} не читаются: {e}", path.display()))?;
    let mut out: BTreeMap<String, Vec<Signal>> = BTreeMap::new();
    for row in r.deserialize::<SignalRow>() {
        let row = row.map_err(|e| anyhow::anyhow!("строка сигналов {}: {e}", path.display()))?;
        let sigma = match row.side.as_str() {
            "bid" => SIGMA_SHORT,
            "ask" => SIGMA_LONG,
            other => anyhow::bail!("сторона сигнала обязана быть bid|ask, получено {other}"),
        };
        out.entry(row.profile_id).or_default().push(Signal {
            t0_ns: row.birth_ms.saturating_mul(1_000_000),
            sigma,
        });
    }
    Ok(out)
}

/// Литерал таска 10 (`profiles.rs::NOT_MEASURED`): колонка измерялась, но
/// источник явно не считал `fill`/`net_fill` (`fill_model=none`) — отличать
/// от `none` (нет данных даже при активной модели).
const NOT_MEASURED: &str = "not_measured";

/// Строка `docs/findings/profiles-<дата>.csv` (таск 10, `commands::lob::
/// profiles`), только нужные этому файлу колонки — `csv`+`serde` матчит по
/// имени заголовка, лишние 22 колонки (`n`, доли исходов, горизонты,
/// `session_start_hours_utc`, …) не мешают. Числовые колонки читаются
/// строкой: `none`/`not_measured` — не `f64`, а два разных отсутствия.
#[derive(Debug, Clone, serde::Deserialize)]
struct ProfilesTableRow {
    profile_id: String,
    net_bps: String,
    net_fill: String,
}

/// `none` → нет числа; `not_measured` → тоже нет числа, но по другой причине
/// (см. `ProfilesTableRow`); иначе — разобранное значение.
fn parse_measured_bps(raw: &str) -> Option<f64> {
    match raw {
        "none" | NOT_MEASURED => None,
        s => s.parse::<f64>().ok(),
    }
}

/// Формат таска 10: шапка — комментарий `# lob profiles: …`, которую
/// `.comment(Some(b'#'))` пропускает целиком (`pick::table::
/// instruments_csv_reader` — тот же приём); эта функция не разбирает её
/// ключи (`fill_model=…`) — то, что нужно, уже несёт каждая строка своим
/// литералом `not_measured`.
fn read_table(path: Option<&Path>) -> anyhow::Result<BTreeMap<String, TableEstimate>> {
    let Some(path) = path else {
        return Ok(BTreeMap::new());
    };
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(path)
        .map_err(|e| anyhow::anyhow!("таблица профилей {} не читается: {e}", path.display()))?;
    let mut out = BTreeMap::new();
    for row in r.deserialize::<ProfilesTableRow>() {
        let row =
            row.map_err(|e| anyhow::anyhow!("строка таблицы профилей {}: {e}", path.display()))?;
        out.insert(
            row.profile_id,
            TableEstimate {
                net_bps: parse_measured_bps(&row.net_bps),
                net_fill_bps: parse_measured_bps(&row.net_fill),
                net_fill_not_measured: row.net_fill == NOT_MEASURED,
            },
        );
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Артефакты: сводка и кривые PnL.
// ---------------------------------------------------------------------------

/// Шапка обоих артефактов (координатор): те же параметры, что реально
/// решают число, плюс `debug` — по решению вызывающего (`--debug`), тем же
/// приёмом, что `lob profiles --allow-unverified`.
fn header_comment(args: &BacktestArgs) -> String {
    let debug_suffix = if args.debug { " debug" } else { "" };
    format!(
        "# lob backtest: rtt_median_ns={} rtt_p95_ns={} order_qty_e9={} alpha={} replications={} seed=0{debug_suffix}",
        args.median_rtt_ns,
        args.p95_rtt_ns,
        args.order_qty_e9,
        crate::stats::GATE_ALPHA,
        crate::stats::BOOTSTRAP_REPLICATIONS,
    )
}

const BACKTEST_HEADER: [&str; 15] = [
    "profile_id",
    "rtt",
    "signals",
    "fills",
    "missed_timeout",
    "missed_busy",
    "incomplete",
    "mean_net_bps",
    "fill",
    "net_fill",
    "net_fill_lower",
    "table_net_bps",
    "table_net_fill_bps",
    "diff_net_fill_bps",
    "g4",
];

#[derive(Debug, Clone, serde::Serialize)]
struct BacktestRow {
    profile_id: String,
    rtt: &'static str,
    signals: u64,
    fills: usize,
    missed_timeout: u64,
    missed_busy: u64,
    incomplete: bool,
    mean_net_bps: Option<f64>,
    fill: Option<f64>,
    net_fill: Option<f64>,
    net_fill_lower: Option<f64>,
    table_net_bps: Option<f64>,
    table_net_fill_bps: Option<f64>,
    diff_net_fill_bps: Option<f64>,
    g4: String,
}

fn write_backtest_csv(path: &Path, report: &BacktestReport, header: &str) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::File::create(path)?;
    writeln!(file, "{header}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(BACKTEST_HEADER)?;
    for p in &report.profiles {
        for (rtt, run) in [("median", &p.median), ("p95", &p.p95)] {
            w.serialize(BacktestRow {
                profile_id: p.profile_id.clone(),
                rtt,
                signals: run.signals,
                fills: run.n_fills(),
                missed_timeout: run.misses.timeout,
                missed_busy: run.misses.busy,
                incomplete: run.incomplete,
                mean_net_bps: run.mean_net_bps(),
                fill: run.fill_rate(),
                net_fill: run.net_fill_bps(),
                net_fill_lower: run.net_fill_interval().map(|iv| iv.lower_bps),
                table_net_bps: p.comparison.table_net_bps,
                table_net_fill_bps: p.comparison.table_net_fill_bps,
                diff_net_fill_bps: p.comparison.diff_net_fill_bps,
                g4: p.g4.to_string(),
            })?;
        }
    }
    w.flush()?;
    Ok(())
}

const PNL_HEADER: [&str; 4] = ["profile_id", "rtt", "step_index", "cum_net_bps"];

#[derive(Debug, Clone, serde::Serialize)]
struct PnlRow {
    profile_id: String,
    rtt: &'static str,
    step_index: usize,
    cum_net_bps: f64,
}

fn write_pnl_csv(path: &Path, report: &BacktestReport, header: &str) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::File::create(path)?;
    writeln!(file, "{header}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    // Заголовок пишется всегда, даже когда ни один профиль не дал ни
    // одного заполнения — координатор: «pnl.csv всегда с заголовком».
    w.write_record(PNL_HEADER)?;
    for p in &report.profiles {
        for (rtt, run) in [("median", &p.median), ("p95", &p.p95)] {
            if let Some(curve) = pnl_curve_bps(&run.fills) {
                for (step_index, cum_net_bps) in curve.into_iter().enumerate() {
                    w.serialize(PnlRow {
                        profile_id: p.profile_id.clone(),
                        rtt,
                        step_index,
                        cum_net_bps,
                    })?;
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Сделка-отскока по касаниям (таск 38, В-44)
// ---------------------------------------------------------------------------

/// План сделки-отскока для касания (В-44, повторено как есть): бид-уровень
/// `P` — покупка лимитом на `P + 1` тик, стоп по рынку на `P − 1`, тейк
/// `вход + (вход − стоп)` = `P + 3` (R 1:1, D 16:17); аск зеркально. Вход
/// снимается в конце касания (`entry_ttl_ns`), позиция закрывается не позже
/// `HORIZONS_MS[3]` (дедлайн 60 с).
fn bounce_plan(
    touch: &TouchRecord,
    tick: f64,
    post_only: bool,
    trail_bps: f64,
    trail_activate_bps: f64,
) -> (i8, TradePlan) {
    let p = touch.price_tick as f64 * tick;
    let entry_ttl_ns = touch
        .end_ms
        .saturating_sub(touch.start_ms)
        .saturating_mul(1_000_000);
    let deadline_ns = HORIZONS_MS[3].saturating_mul(1_000_000);
    match touch.side {
        Side::Bid => (
            SIGMA_LONG,
            TradePlan::Bounce {
                entry_px: p + tick,
                stop_px: p - tick,
                take_px: p + 3.0 * tick,
                deadline_ns,
                entry_ttl_ns,
                post_only,
                trail_bps,
                trail_activate_bps,
            },
        ),
        Side::Ask => (
            SIGMA_SHORT,
            TradePlan::Bounce {
                entry_px: p - tick,
                stop_px: p + tick,
                take_px: p - 3.0 * tick,
                deadline_ns,
                entry_ttl_ns,
                post_only,
                trail_bps,
                trail_activate_bps,
            },
        ),
    }
}

/// Строка отчёта: «профиль» — либо `все`, либо `ось:корзина`. Корзины —
/// существующие (`touch_axes`, `SIZE_LABELS`), новых границ здесь нет;
/// касание может попасть сразу в несколько строк, и это правильно: строки —
/// маргиналы осей, как в T37.
struct BounceRow {
    profile: String,
    touches: Vec<usize>,
}

fn bounce_rows(touches: &[TouchRecord], h3_lots: i64) -> Vec<BounceRow> {
    let mut rows = vec![BounceRow {
        profile: "все".to_string(),
        touches: (0..touches.len()).collect(),
    }];
    let push = |rows: &mut Vec<BounceRow>, axis: &str, bucket: &str, i: usize| {
        let name = format!("{axis}:{bucket}");
        match rows.iter_mut().find(|r| r.profile == name) {
            Some(r) => r.touches.push(i),
            None => rows.push(BounceRow {
                profile: name,
                touches: vec![i],
            }),
        }
    };
    for (i, t) in touches.iter().enumerate() {
        if let Some(b) = age_bucket(t.start_ms.saturating_sub(t.level_birth_ms)) {
            push(&mut rows, "возраст", b, i);
        }
        if h3_lots > 0 {
            if let Some(b) = size_bucket(t.size_at_touch as f64 / h3_lots as f64) {
                push(&mut rows, "размер", b, i);
            }
        }
        if let Some(share) = frontrun_share(t.frontrun_lots, t.size_at_touch) {
            if let Some(b) = frontrun_bucket(share) {
                push(&mut rows, "фронтран", b, i);
            }
        }
        push(&mut rows, "круглость", round_bucket(t.round_zeros), i);
        push(&mut rows, "номер", touch_index_bucket(t.touch_index), i);
        push(&mut rows, "сторона", side_name(t.side), i);
    }
    rows
}

/// Прогон сделки-отскока по касаниям символа и отчёт по осям В-44.
fn run_bounce(
    args: &BacktestArgs,
    binlog_paths: &[PathBuf],
    tick_e9: i64,
    step_e9: i64,
    events: &[HbtEvent],
) -> anyhow::Result<BacktestSummary> {
    let tick = tick_e9 as f64 / 1e9;
    let lot_size = step_e9 as f64 / 1e9;
    let order_qty = args.order_qty_e9 as f64 / 1e9;

    // Порог `H3` — тем же резолвером, что `lob touches`/`levels`: числа
    // считаются тем же кодом, что CSV касаний.
    let mode = super::resolve_h3_mode_with_k(
        &args.session_root,
        &args.symbol,
        args.h3.h3_mode,
        args.h3.h3_lots,
        args.h3_k,
    )?;
    let cfg_levels = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms.unwrap_or(super::DEFAULT_WARMUP_MS),
        repeat_window_ms: args
            .repeat_window_ms
            .unwrap_or(super::DEFAULT_REPEAT_WINDOW_MS),
    };
    let h3_lots = match mode {
        H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => h3_lots,
    };
    let replay = super::replay_symbol(&args.session_root, &args.symbol, cfg_levels)?;
    let mut touches: Vec<TouchRecord> = Vec::new();
    for day in &replay.days {
        touches.extend(day.touches.iter().cloned());
    }
    anyhow::ensure!(
        !touches.is_empty(),
        "касаний нет в {}: разметка пуста или порог не дал уровней",
        args.session_root.display()
    );

    // Сигналы в порядке касаний; движок сортирует их по времени сам, и порядок
    // его сортировки здесь повторяется (`sort_by_key` стабилен) — по нему
    // круг относится к касанию, без второго прогона на каждую ось.
    let signals: Vec<BounceSignal> = touches
        .iter()
        .map(|t| {
            let (sigma, plan) = bounce_plan(
                t,
                tick,
                args.post_only,
                args.trail_bps,
                args.trail_activate_bps,
            );
            BounceSignal {
                t0_ns: t.start_ms.saturating_mul(1_000_000),
                sigma,
                plan,
                profile: 0,
            }
        })
        .collect();
    let mut order: Vec<usize> = (0..signals.len()).collect();
    order.sort_by_key(|&i| signals[i].t0_ns);

    let cfg = DriveConfig {
        order_qty,
        first_order_id: 1,
    };
    let mut bt = build_backtest(events, tick, lot_size, args.median_rtt_ns);
    let run: BounceRun = drive_bounce(&mut bt, 0, &signals, &cfg)?;

    let rows = bounce_rows(&touches, h3_lots);
    let date = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/bounce-backtest-{date}.csv")));
    let pnl_out = args
        .pnl_out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/bounce-backtest-{date}-pnl.csv")));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }

    let header = format!(
        "# lob backtest --touches: {} {} RTT={}нс assumed(В-37), сделка-отскок В-44 (вход за тик, стоп за тик внутрь, тейк 1:1, дедлайн {} мс), порог H3={} лотов, касаний {}, бинлогов {}",
        args.symbol,
        args.session_root.display(),
        args.median_rtt_ns,
        HORIZONS_MS[3],
        h3_lots,
        touches.len(),
        binlog_paths.len()
    );
    // Шапка артефакта — строкой комментария: чем посчитан файл (`rtt=assumed`
    // В-37, порог `H3`, состав сделки). Пишется до таблицы, как у остальных
    // артефактов вердикта.
    let mut file = std::fs::File::create(&out)?;
    use std::io::Write as _;
    writeln!(file, "# {header}")?;
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "profile_id",
        "n_signals",
        "n_submitted",
        "n_filled",
        "fill",
        "n_busy",
        "n_entry_timeout",
        "n_stop",
        "n_take",
        "n_timeout",
        "n_trail",
        "net_bps",
        "net_fill_bps",
        "net_fill_lo_bps",
        "net_fill_point_bps",
        "days",
        "incomplete",
    ])?;
    let mut in_pos: Vec<usize> = vec![0; signals.len()];
    for (pos, &idx) in order.iter().enumerate() {
        in_pos[idx] = pos;
    }
    let mut all_fills: Vec<crate::lob::backtest::Fill> = Vec::new();
    for row in &rows {
        let positions: Vec<usize> = row.touches.iter().map(|&i| in_pos[i]).collect();
        let mut obs = Vec::with_capacity(positions.len());
        for &p in &positions {
            if let Some(o) = run.observations.get(p) {
                obs.push(*o);
            }
        }
        let fills: Vec<crate::lob::backtest::Fill> = run
            .fills
            .iter()
            .zip(&run.fill_signal)
            .filter(|(_, &s)| positions.contains(&s))
            .map(|(f, _)| *f)
            .collect();
        let reasons: Vec<&crate::lob::strategy::ExitReason> = run
            .fill_reason
            .iter()
            .zip(&run.fill_signal)
            .filter(|(_, &s)| positions.contains(&s))
            .map(|(r, _)| r)
            .collect();
        let n_stop = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Stop))
            .count();
        let n_take = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Take))
            .count();
        let n_timeout = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Deadline))
            .count();
        let n_trail = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Trail))
            .count();
        let interval = net_fill_interval(
            &obs,
            crate::stats::GATE_ALPHA,
            crate::stats::BOOTSTRAP_REPLICATIONS,
            0,
        );
        let num = |v: Option<f64>| match v {
            Some(x) => format!("{x:.6}"),
            None => "—".to_string(),
        };
        let mut days: Vec<i64> = obs.iter().map(|o| o.day_cluster).collect();
        days.sort_unstable();
        days.dedup();
        // Доля исполнения — от **отправленных** входов, а не от всех касаний:
        // касание, пропущенное из-за занятой позиции, не «неисполненный
        // вход» (находка прогона 2026-09-13, смешивать их нельзя).
        let n_submitted = positions
            .iter()
            .filter(|p| run.submitted_signal.binary_search(p).is_ok())
            .count();
        let n_busy = positions
            .iter()
            .filter(|p| run.busy_signal.binary_search(p).is_ok())
            .count();
        let fill_share = if n_submitted > 0 {
            Some(fills.len() as f64 / n_submitted as f64)
        } else {
            None
        };
        w.write_record([
            row.profile.clone(),
            positions.len().to_string(),
            n_submitted.to_string(),
            fills.len().to_string(),
            num(fill_share),
            n_busy.to_string(),
            n_submitted.saturating_sub(fills.len()).to_string(),
            n_stop.to_string(),
            n_take.to_string(),
            n_timeout.to_string(),
            n_trail.to_string(),
            num(mean_net_bps(&fills)),
            num(net_fill_bps(&obs)),
            num(interval.map(|i| i.lower_bps)),
            num(interval.map(|i| i.point_bps)),
            days.len().to_string(),
            run.incomplete.to_string(),
        ])?;
        if row.profile == "все" {
            all_fills = fills;
        }
    }
    w.flush()?;

    // Кривая PnL — по всем кругам прогона (одна на символ, не на ось).
    let mut wp = csv::Writer::from_path(&pnl_out)?;
    wp.write_record(["n", "cum_net_bps"])?;
    if let Some(curve) = pnl_curve_bps(&all_fills) {
        for (i, v) in curve.iter().enumerate() {
            wp.write_record([(i + 1).to_string(), format!("{v:.6}")])?;
        }
    }
    wp.flush()?;

    // Замеры механизма (таск 38): почему вход не исполняется — по книге и по
    // статусам ордеров, а не по догадке из чужого исходника.
    let mut hist = [0u64; 4]; // спред 1, 2, 3, 4+ тика
    for s in &run.spread_at_entry {
        let t = (s / tick).round().max(1.0) as usize;
        hist[(t - 1).min(3)] += 1;
    }
    let sent = run.spread_at_entry.len().max(1) as f64;
    println!(
        "bounce: вход отправлен {} раз · пересекал спред {} ({:.1}%) · отвергнуто биржей {} · спред на входе: 1 тик {:.1}%, 2 {:.1}%, 3 {:.1}%, ≥4 {:.1}%",
        run.spread_at_entry.len(),
        run.entry_crossed,
        100.0 * run.entry_crossed as f64 / sent,
        run.entry_rejected,
        100.0 * hist[0] as f64 / sent,
        100.0 * hist[1] as f64 / sent,
        100.0 * hist[2] as f64 / sent,
        100.0 * hist[3] as f64 / sent,
    );
    println!(
        "bounce: промахи раздельно — вход не исполнен {} · позиция занята {} · самая долгая блокировка {:.1} с · самая долгая жизнь круга {:.1} с",
        run.misses.timeout,
        run.misses.busy,
        run.busy_wait_ns_max as f64 / 1e9,
        run.round_ns_max as f64 / 1e9,
    );
    println!(
        "bounce: касаний {} · кругов {} · стоп {} · тейк {} · трейл {} · дедлайн {} · промахи {} · incomplete {}",
        touches.len(),
        run.fills.len(),
        run.exits.stop,
        run.exits.take,
        run.exits.trail,
        run.exits.deadline,
        run.misses.total(),
        run.incomplete
    );
    println!(
        "bounce: строк {} → {} (+ кривые PnL {})",
        rows.len(),
        out.display(),
        pnl_out.display()
    );

    let pass = 0;
    Ok(BacktestSummary {
        profiles: rows.len(),
        pass,
        red: rows.len(),
        out,
        pnl_out,
    })
}

#[cfg(test)]
mod tests;
