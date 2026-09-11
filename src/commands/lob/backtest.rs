//! `lob backtest` — CLI-обёртка вокруг движка `lob::backtest` (таск 11,
//! спека — истории 32–34, §6). Единственная зона, которой разрешено знать
//! одновременно про `Feed`/`bybit::ws` (площадка) и про `hftbacktest::types::
//! Event` (нейтральный формат крейта) — граница «чистое ядро стратегии не
//! тянет транспорт и площадку» держится тестом-грепом в `lob/backtest.rs`,
//! а перевод одного формата в другой поэтому живёт здесь, а не там.
//!
//! Вход — `ReplayFeed` по бинлогу одной сессии одного символа
//! (`data/session-debug/<ts>/<SYMBOL>.binlog`, `data/recon5/<SYMBOL>/…` —
//! отладочные, метка `debug`, `CLAUDE.md`). Сигналы (какие уровни торговать
//! и какой стороной) приходят отдельным CSV — этот файл их не размечает и
//! не классифицирует по осям семи профилей (это `lob levels`/будущая
//! проводка таска 12); здесь только: `profile_id,side,birth_ms` — сторона
//! `bid`/`ask` (Decision 14) и момент срабатывания в мс, тот же смысл, что
//! колонка `birth_ms` артефакта `lob levels`.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog;
use crate::bybit::ws::Event as WsEvent;
use crate::feed::{replay::ReplayFeed, Event as FeedEvent, Feed};
use crate::lob::backtest::{
    build_backtest, build_profile_report, drive_profile, pnl_curve_bps, BacktestReport,
    DriveConfig, Signal, TableEstimate, SIGMA_LONG, SIGMA_SHORT,
};
use hftbacktest::types::{
    Event as HbtEvent, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT,
    EXCH_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BUY_TRADE_EVENT, LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

// ---------------------------------------------------------------------------
// `lob backtest` (шаг 6.3, история 32–34).
// ---------------------------------------------------------------------------

/// Аргументы `lob backtest`: вердикт бэктеста с моделью очереди на
/// произвольное число профилей.
#[derive(Debug, Args)]
pub struct BacktestArgs {
    /// Корень сессии: файл `<SYMBOL>.binlog` (см. `lob session`).
    #[arg(long)]
    pub session_root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// CSV сигналов: `profile_id,side,birth_ms` (`side`: `bid`|`ask` —
    /// Decision 14; `birth_ms` — момент срабатывания, тот же смысл, что
    /// колонка `birth_ms` артефакта `lob levels`). Несколько строк одного
    /// `profile_id` — один профиль.
    #[arg(long)]
    pub signals_csv: PathBuf,
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
    let binlog_path = args.session_root.join(format!("{}.binlog", args.symbol));
    let (tick_e9, step_e9) = read_tick_step(&binlog_path)?;
    let tick_size = tick_e9 as f64 / 1e9;
    let lot_size = step_e9 as f64 / 1e9;
    let order_qty = args.order_qty_e9 as f64 / 1e9;

    let mut feed = open_replay_feed(&binlog_path)?;
    let events = events_from_feed(&mut feed);
    if events.is_empty() {
        anyhow::bail!("бинлог {} пуст или не разобрался", binlog_path.display());
    }

    let signals = read_signals(&args.signals_csv)?;
    if signals.is_empty() {
        anyhow::bail!("сигналов нет: {}", args.signals_csv.display());
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::book::Update;

    struct VecFeed(std::vec::IntoIter<FeedEvent>);
    impl Feed for VecFeed {
        fn next_event(&mut self) -> Option<FeedEvent> {
            self.0.next()
        }
    }

    fn book_ev(local_ts_ns: i64, up: Update) -> FeedEvent {
        FeedEvent::Market {
            symbol: 0,
            local_ts_ns,
            parse_latency_ns: None,
            payload: WsEvent::Book(up),
        }
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// Снапшот, потерявший уровень против предыдущего снапшота, обязан
    /// явно его обнулить — иначе `HashMapMarketDepth` крейта унаследует
    /// цену, которой в книге уже нет (`book::Book::apply` делает то же самое
    /// перед применением снапшота, это тот же случай на другом типе).
    #[test]
    fn snapshot_clears_stale_levels_and_deltas_upsert() {
        let snap1 = Update {
            is_snapshot: true,
            u: 1,
            seq: 1,
            cts_ms: 1000,
            bids: vec![
                (100_000_000_000, 5_000_000_000),
                (99_000_000_000, 2_000_000_000),
            ],
            asks: vec![(101_000_000_000, 3_000_000_000)],
        };
        let snap2 = Update {
            is_snapshot: true,
            u: 2,
            seq: 2,
            cts_ms: 2000,
            bids: vec![(100_000_000_000, 4_000_000_000)],
            asks: vec![(101_000_000_000, 3_000_000_000)],
        };
        let events = vec![book_ev(1_500, snap1), book_ev(2_500, snap2)];
        let mut feed = VecFeed(events.into_iter());
        let out = events_from_feed(&mut feed);

        assert_eq!(
            out.iter().filter(|e| e.exch_ts == 1_000_000_000).count(),
            3,
            "первый снапшот — только апсерты, стейла ещё нет"
        );
        let cleared = out
            .iter()
            .find(|e| e.exch_ts == 2_000_000_000 && close(e.px, 99.0));
        assert!(
            cleared.is_some(),
            "пропавший из второго снапшота уровень (99.0) обязан обнулиться"
        );
        assert_eq!(cleared.unwrap().qty, 0.0);
        let kept = out
            .iter()
            .rfind(|e| e.exch_ts == 2_000_000_000 && close(e.px, 100.0))
            .unwrap();
        assert_eq!(kept.qty, 4.0, "выживший уровень апсерчен новым размером");
    }

    /// Дельта с нулевым размером снимает уровень так же, как отсутствие
    /// его в снапшоте — оба пути дают одно и то же событие цепочки крейта.
    #[test]
    fn zero_qty_delta_removes_a_level() {
        let snap = Update {
            is_snapshot: true,
            u: 1,
            seq: 1,
            cts_ms: 1000,
            bids: vec![(100_000_000_000, 5_000_000_000)],
            asks: vec![],
        };
        let delta = Update {
            is_snapshot: false,
            u: 2,
            seq: 2,
            cts_ms: 1500,
            bids: vec![(100_000_000_000, 0)],
            asks: vec![],
        };
        let events = vec![book_ev(1_000, snap), book_ev(1_200, delta)];
        let mut feed = VecFeed(events.into_iter());
        let out = events_from_feed(&mut feed);
        assert_eq!(out.len(), 2);
        assert_eq!(out[1].qty, 0.0);
        assert!(close(out[1].px, 100.0));
    }

    /// Реальная шапка `docs/findings/profiles-<дата>.csv` (таск 10,
    /// `commands::lob::profiles::HEADER`/`write_row`), записанная литералом:
    /// комментарий `#`, 26 колонок, `not_measured` там, где `fill_model=none`
    /// не мерил `fill`/`net_fill`. Не старая схема `shortlist::ProfileRow`,
    /// которую этот читатель больше не понимает (ревью: не соединялась).
    const PROFILES_TASK10_FIXTURE: &str = concat!(
        "# lob profiles: h3_mode=floor warmup_ms=3600000 repeat_window_ms=3600000",
        " alpha=0.005 replications=9999 seed=0 fill_model=none debug\n",
        "profile_id,n,eaten_share,pulled_share,mixed_share,",
        "m_100ms,m_100ms_lower,raw_100ms,unreachable_100ms,",
        "m_1000ms,m_1000ms_lower,raw_1000ms,unreachable_1000ms,",
        "m_10000ms,m_10000ms_lower,raw_10000ms,unreachable_10000ms,",
        "m_60000ms,m_60000ms_lower,raw_60000ms,unreachable_60000ms,",
        "net_bps,fill,net_fill,net_fill_lower,session_start_hours_utc\n",
        "smoke:bid,150,0.3000,0.6000,0.1000,",
        "1.0000,0.5000,1.0000,not_measured,",
        "2.0000,1.0000,2.0000,not_measured,",
        "3.0000,1.0000,3.0000,not_measured,",
        "4.0000,1.0000,4.0000,not_measured,",
        "10.0000,not_measured,not_measured,not_measured,2\n",
        "smoke:ask,120,0.2000,0.7000,0.1000,",
        "1.0000,0.5000,1.0000,not_measured,",
        "2.0000,1.0000,2.0000,not_measured,",
        "3.0000,1.0000,3.0000,not_measured,",
        "4.0000,1.0000,4.0000,not_measured,",
        "5.0000,0.4000,3.0000,1.5000,3\n",
    );

    /// Таблица профилей читается по пути-параметру на фикстуре формата
    /// таска 10, не на боевом файле. `smoke:bid` — колонка `net_fill`
    /// `not_measured` (`fill_model=none`): читатель обязан пометить это
    /// явно, а не молчать как `None`. `smoke:ask` — обычная измеренная
    /// строка, для контраста. Отсутствующий профиль и путь — пусто, не
    /// паника.
    #[test]
    fn read_table_parses_task10_format_and_flags_not_measured() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("profiles-fixture.csv");
        std::fs::write(&path, PROFILES_TASK10_FIXTURE).unwrap();

        let table = read_table(Some(&path)).unwrap();

        let bid = table.get("smoke:bid").expect("профиль обязан найтись");
        assert_eq!(bid.net_bps, Some(10.0));
        assert_eq!(bid.net_fill_bps, None, "not_measured — не число");
        assert!(bid.net_fill_not_measured, "литерал обязан распознаться");

        let ask = table.get("smoke:ask").expect("профиль обязан найтись");
        assert_eq!(ask.net_bps, Some(5.0));
        assert_eq!(ask.net_fill_bps, Some(3.0));
        assert!(!ask.net_fill_not_measured, "измеренная строка — не флаг");

        assert!(!table.contains_key("smoke:missing"), "чужого профиля нет");
        assert!(
            read_table(None).unwrap().is_empty(),
            "без пути — пустая таблица, не паника"
        );
    }

    /// Сквозная проверка сравнения на фикстуре таска 10: `net_fill`
    /// `not_measured` обязан упасть сравнением на `net_bps` (координатор), а
    /// не молча дать `None` разности.
    #[test]
    fn compare_with_table_falls_back_to_net_bps_on_the_task10_fixture() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("profiles-fixture.csv");
        std::fs::write(&path, PROFILES_TASK10_FIXTURE).unwrap();
        let table = read_table(Some(&path)).unwrap();

        let comparison =
            crate::lob::backtest::compare_with_table(table.get("smoke:bid").copied(), Some(92.5));
        assert!(
            (comparison.diff_net_fill_bps.unwrap() - 82.5).abs() < 1e-9,
            "{comparison:?}"
        );
        assert!(comparison.format_line().contains("not_measured"));
    }
}
