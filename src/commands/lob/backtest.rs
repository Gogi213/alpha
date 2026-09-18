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
    roundtrip_net_bps, BacktestReport, BounceRun, BounceSignal, DriveConfig, Signal, TableEstimate,
    SIGMA_LONG, SIGMA_SHORT,
};
use crate::lob::costs::{net_fill_bps, net_fill_interval};
use crate::lob::levels::{LevelRecord, LevelsConfig, TouchRecord};
use crate::lob::markout::MidSample;
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
    /// не измеренным (§9 плана). Взаимоисключающий с `--order-qty-from-pool`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
    /// Считать размер круга `order_size_22a` от полей пула `instruments.csv`
    /// сессии и цены последнего касания реплея (Decision 22а). Нужен там, где
    /// лота символа нет в журнале `candidates.csv`: у замороженного пула на 100
    /// монет (отбор 2026-09-15) таблица кандидатов не коммитилась, а шаг книги —
    /// другая величина. Только с `--touches`: цену даёт реплей касаний.
    #[arg(long, default_value_t = false)]
    pub order_qty_from_pool: bool,
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
    /// Вход лестницей: сколько лимитов ставить вместо одного (1 — как было).
    /// Решение владельца 2026-09-13.
    #[arg(long, default_value_t = 1)]
    pub grid_legs: u8,
    /// Шаг лестницы в тиках (0 — все ноги по одной цене).
    #[arg(long, default_value_t = 0)]
    pub grid_step_ticks: i64,
    /// Форма стопа базы (В-65): `before|at|behind|midfr|stack2|pct<x>|s<a>`.
    /// Обязательна при `--touches`. Вход берётся от первого фронтранера
    /// касания, если он был (`TouchRecord::frontrun_tick`), иначе `P+1` тик.
    #[arg(long)]
    pub stop_form: Option<String>,
    /// Форма тейка: `1to1` (от входа) или `t<b>` (`b × σ_H`). Обязательна при `--touches`.
    #[arg(long)]
    pub take_form: Option<String>,
    /// Пол σ-тейка в кругах комиссий (`costs::ROUNDTRIP_FEES_BPS`, В-62/В-63);
    /// нужен только форме `t<b>`.
    #[arg(long)]
    pub take_floor_fees: Option<f64>,
    /// Дедлайн сделки, секунды (B3, В-58 п. 4) — из предрегистрированной
    /// сетки {60, 600, 3600, 7200}: «S и S-D; S-D значит от секунд до, наверно,
    /// пары часов» (ответ владельца 2). Другое значение — отказ: сетка
    /// зафиксирована **до** данных, и «попробовать ещё одно» — это лишнее
    /// испытание, а не параметр (В-58: каждое дополнительно рассмотренное
    /// значение считается отдельным испытанием).
    #[arg(long, default_value_t = 60)]
    pub deadline_secs: i64,
    /// Досрочный выход «по прилипанию» (B4, В-58 п. 5): секунды `X` из
    /// предрегистрированного набора {1, 2, 3} — если через `X` после входа
    /// уровень **всё ещё лучшая цена** (касание не разрешилось ни в отскок, ни
    /// в пробой), выходим по рынку. Отсутствие флага — выход выключен, и это
    /// четвёртый вариант той же оси В-58 («выключен либо X ∈ {1, 2, 3}»).
    /// Другое значение — отказ: сетка зафиксирована **до** данных.
    #[arg(long)]
    pub early_exit_secs: Option<i64>,
    /// Покруговой дамп прогона `--touches` (B5, В-58): одна строка на закрытый
    /// круг профиля «все» — обе ноги, размер, чистая доходность в bps и причина
    /// выхода. Прокруговой ряд нужен прогону-вердикту (`lob bounce-verdict`):
    /// поправка на число испытаний дефлирует Шарп **ряда**, и по средним из
    /// сводки его не посчитать. Без флага файл не пишется — у прогонов B2/B4
    /// артефакт остаётся прежним.
    #[arg(long)]
    pub trades_out: Option<PathBuf>,
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
    /// Снять требование маркера сверки `verify-<SYMBOL>.status == ok` (отладочные
    /// данные; К1 аудита 18.09).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
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
    super::require_verified(&args.session_root, &args.symbol, args.allow_unverified)?;
    let binlog_paths = super::session_binlog_for(&args.session_root, &args.symbol)?;
    let (tick_e9, step_e9) = read_tick_step(&binlog_paths[0])?;
    let tick_size = tick_e9 as f64 / 1e9;
    let lot_size = step_e9 as f64 / 1e9;

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
    // Профильный путь: лот задаётся флагом (лот из пула считается по цене
    // касаний, которых здесь нет) — разрешение после ветки `--touches`.
    let order_qty_e9 = order_qty_arg(args)?;
    let order_qty = order_qty_e9 as f64 / 1e9;
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

    let header = header_comment(args, order_qty_e9);
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

pub(crate) fn read_tick_step(path: &Path) -> anyhow::Result<(i64, i64)> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
    let reader = binlog::Reader::open(file)
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    Ok((header.tick_e9, header.step_e9))
}

pub(crate) fn open_replay_feed(path: &Path) -> anyhow::Result<ReplayFeed<std::fs::File>> {
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
pub(crate) fn events_from_feed(feed: &mut dyn Feed) -> Vec<HbtEvent> {
    let mut out = Vec::new();
    feed_events_into(feed, &mut out);
    out
}

/// Сколько событий крейта даст `feed` — тот же перевод, что `feed_events_into`,
/// но в счётчик. Нужен, чтобы выделить `Vec` суток **один раз** точного
/// размера: рост удвоением держал бы старый и новый буфер вместе (до 3×
/// итога), и на сутках в 20 млн событий это 3 ГБ — сетка на сервере умирала
/// по OOM (2026-09-18, `alpha-grid-20260917`, лимит 2.6 ГБ). Второй декод
/// стоит ~0.16 мкс на событие — дешевле памяти.
pub(crate) fn count_feed_events(feed: &mut dyn Feed) -> usize {
    let mut n = 0usize;
    translate_feed(feed, &mut |_| n += 1);
    n
}

/// Перевод `feed` → события крейта в готовый `Vec` (без промежуточного).
pub(crate) fn feed_events_into(feed: &mut dyn Feed, out: &mut Vec<HbtEvent>) {
    translate_feed(feed, &mut |ev| out.push(ev));
}

fn translate_feed(feed: &mut dyn Feed, sink: &mut impl FnMut(HbtEvent)) {
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
                    sink,
                    &mut known_bids,
                    &up.bids,
                    up.is_snapshot,
                    exch_ts,
                    local_ts_ns,
                    true,
                );
                push_side(
                    sink,
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
                sink(HbtEvent {
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
            WsEvent::Other | WsEvent::SubscribeFailed { .. } => {}
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn push_side(
    sink: &mut impl FnMut(HbtEvent),
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
        sink(HbtEvent {
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
fn header_comment(args: &BacktestArgs, order_qty_e9: i64) -> String {
    let debug_suffix = if args.debug { " debug" } else { "" };
    format!(
        "# lob backtest: rtt_median_ns={} rtt_p95_ns={} order_qty_e9={} alpha={} replications={} seed=0{debug_suffix}",
        args.median_rtt_ns,
        args.p95_rtt_ns,
        order_qty_e9,
        crate::stats::GATE_ALPHA,
        crate::stats::BOOTSTRAP_REPLICATIONS,
    )
}

/// Явный размер круга профильного пути. Лот из пула (`--order-qty-from-pool`)
/// считается по цене касаний реплея, а профильный путь касаний не строит:
/// отказ, а не подстановка шага книги вместо лота площадки.
fn order_qty_arg(args: &BacktestArgs) -> anyhow::Result<i64> {
    match (args.order_qty_e9, args.order_qty_from_pool) {
        (Some(v), false) => Ok(v),
        (None, false) => anyhow::bail!(
            "нужен --order-qty-e9 (либо --order-qty-from-pool вместе с --touches): \
             изобретённого умолчания нет (§9 плана)"
        ),
        (_, true) => anyhow::bail!(
            "--order-qty-from-pool работает только с --touches: цену для `order_size_22a` \
             даёт реплей касаний"
        ),
    }
}

/// Размер круга `order_size_22a` (Decision 22а) от полей пула сессии и цены
/// последнего касания: `max(minOrderQty, ceil(minNotional / (step × price)) ×
/// step)`. Цена измерена на тех же данных, что и вердикт, — второй проход за
/// ценой не нужен (тот же приём, что у пилота с последней строкой
/// `levels-floor`). Нет символа в пуле — отказ: лот обязан быть названным
/// числом, а не шагом книги.
pub(crate) fn pool_order_qty(
    instruments_csv: &Path,
    symbol: &str,
    last_price_tick: i64,
    tick_e9: i64,
) -> anyhow::Result<i64> {
    #[derive(Debug, serde::Deserialize)]
    struct Row {
        symbol: String,
        min_order_qty: String,
        qty_step: String,
        min_notional_value: String,
    }
    let mut r = super::pick::instruments_csv_reader(instruments_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
    for row in r.deserialize::<Row>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
        if row.symbol != symbol {
            continue;
        }
        let e9 = |name: &str, v: &str| {
            crate::bybit::ws::parse_e9(v)
                .ok_or_else(|| anyhow::anyhow!("{symbol}: {name} `{v}` не разобрался как 1e-9"))
        };
        let min_order_qty_e9 = e9("min_order_qty", &row.min_order_qty)?;
        let qty_step_e9 = e9("qty_step", &row.qty_step)?;
        let min_notional_value_e9 = e9("min_notional_value", &row.min_notional_value)?;
        let last_price_e9 = last_price_tick.saturating_mul(tick_e9);
        return Ok(super::pick::order_size_22a(
            min_order_qty_e9,
            qty_step_e9,
            min_notional_value_e9,
            last_price_e9,
        ));
    }
    anyhow::bail!(
        "{symbol}: нет в {} — лот из пула взять негде",
        instruments_csv.display()
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

/// Покруговой дамп прогона `--touches` (`--trades-out`): одна строка на
/// закрытый круг профиля «все». Порядок строк — порядок исполнения (тот же,
/// что у кривой PnL), поэтому ряд пригоден и для Шарпа, и для кумулятивной
/// суммы. `net_bps` не посчитался — литерал `not_measured`, не пустая строка
/// и не ноль: «нет числа» и «ноль» — разные вещи (то же правило, что у
/// `TableEstimate`), а потребитель (`lob bounce-verdict`) на литерале
/// отказывает, а не молча выкидывает круг.
fn write_trades_csv(path: &Path, header: &str, run: &BounceRun) -> anyhow::Result<()> {
    anyhow::ensure!(
        run.fill_reason.len() == run.fills.len() && run.fill_signal.len() == run.fills.len(),
        "кругов {}, причин выхода {}, сигналов {}: дамп не пишется из несогласованного прогона",
        run.fills.len(),
        run.fill_reason.len(),
        run.fill_signal.len()
    );
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut file = std::fs::File::create(path)?;
    writeln!(file, "{header}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record([
        "signal_index",
        "dir",
        "entry_px",
        "exit_px",
        "qty",
        "net_bps",
        "reason",
    ])?;
    for (i, fill) in run.fills.iter().enumerate() {
        let net = match roundtrip_net_bps(fill) {
            Some(v) => format!("{v:.6}"),
            None => "not_measured".to_string(),
        };
        w.write_record([
            run.fill_signal[i].to_string(),
            fill.dir.to_string(),
            format!("{:.10}", fill.entry_px),
            format!("{:.10}", fill.exit_px),
            format!("{:.10}", fill.qty),
            net,
            exit_reason_label(run.fill_reason[i]).to_string(),
        ])?;
    }
    w.flush()?;
    Ok(())
}

/// Причина выхода словами — тот же словарь, что `lob bounce-verdict` и
/// `EXIT_REASONS`: одна форма имени на писателя и читателя, чтобы отчёт не
/// собирался из двух разных написаний одной причины.
pub(crate) fn exit_reason_label(reason: crate::lob::strategy::ExitReason) -> &'static str {
    use crate::lob::strategy::ExitReason;
    match reason {
        ExitReason::Stop => "stop",
        ExitReason::Take => "take",
        ExitReason::Trail => "trail",
        ExitReason::Deadline => "deadline",
        ExitReason::Early => "early",
        ExitReason::Horizon => "horizon",
    }
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

/// Форма стопа сделки-отскока — **база из файлов «отскок»** (E6, В-64/В-65)
/// плюс σ (В-62) для сравнения. Позиционные формы считаются от цены уровня
/// `P` и цены входа (первый фронтранер, иначе `P ± 1` тик). Форма, которую
/// для касания построить нельзя (нет фронтрана, нет второй плотности завала,
/// нет `σ`, стоп совпал бы со входом или лёг по ту же сторону), сигнала не
/// даёт — `bounce_plan` возвращает `None`, вызывающий считает пропуск.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum StopForm {
    /// Перед плотностью: `P + 1` тик для бида — «остопиться в плотность, а
    /// лучше перед ней за один тик» [D 12:56], «стоп я кидал перед этой
    /// крупной заявкой» [S 31:26]. Нужен вход от фронтрана дальше тика.
    Before,
    /// В плотность: `P` [D 12:56].
    At,
    /// За плотностью: `P − 1` (форма T38; у практиков — только «мёртвая
    /// монета» [D 12:20]).
    Behind,
    /// Середина фронтрана: посередине между входом и `P` (пять положений
    /// стопа, [K 18:00]); нужен фронтран дальше двух тиков.
    MidFrontrun,
    /// За второй плотностью завала: тик за `stack_next_tick` — «стоп за
    /// первую-вторую плотность» [D 13:41]; нужна вторая плотность в окне.
    BehindStack,
    /// Процент от входа: «от фронтрана до стопа 0,5–2 %» [D 06:33].
    Pct(f64),
    /// `a × σ_H` bps от входа, не ближе тика за плотностью (В-62, наша форма).
    Sigma(f64),
}

impl StopForm {
    /// Имя формы в сетке и в `runs.csv`: `before|at|behind|midfr|stack2|pct<x>|s<a>`.
    pub fn label(self) -> String {
        match self {
            Self::Before => "before".to_string(),
            Self::At => "at".to_string(),
            Self::Behind => "behind".to_string(),
            Self::MidFrontrun => "midfr".to_string(),
            Self::BehindStack => "stack2".to_string(),
            Self::Pct(x) => format!("pct{x}"),
            Self::Sigma(a) => format!("s{a}"),
        }
    }

    /// Разбор имени; неканоническое (`s1.0`, `pct01`) — отказ.
    pub fn parse(label: &str) -> anyhow::Result<Self> {
        let form = match label {
            "before" => Self::Before,
            "at" => Self::At,
            "behind" => Self::Behind,
            "midfr" => Self::MidFrontrun,
            "stack2" => Self::BehindStack,
            _ => {
                if let Some(rest) = label.strip_prefix("pct") {
                    Self::Pct(parse_mult(rest, label)?)
                } else if let Some(rest) = label.strip_prefix('s') {
                    Self::Sigma(parse_mult(rest, label)?)
                } else {
                    anyhow::bail!(
                        "{label}: форма стопа не из базы (before|at|behind|midfr|stack2|pct<x>|s<a>)"
                    )
                }
            }
        };
        anyhow::ensure!(
            form.label() == label,
            "{label}: имя формы стопа не каноническое (ожидалось {})",
            form.label()
        );
        form.validate()?;
        Ok(form)
    }

    fn validate(self) -> anyhow::Result<()> {
        match self {
            Self::Pct(x) => anyhow::ensure!(
                x.is_finite() && x > 0.0 && x < 100.0,
                "pct{x}: процент стопа обязан быть в (0, 100)"
            ),
            Self::Sigma(a) => anyhow::ensure!(
                a.is_finite() && a >= 0.0,
                "s{a}: множитель σ стопа — конечное число ≥ 0"
            ),
            _ => {}
        }
        Ok(())
    }
}

/// Форма тейка: `1:1` от входа — единственная общая у практиков [D 16:17]
/// («тейк частями» — E7, после стопов), либо `b × σ_H` (В-62).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TakeForm {
    OneToOne,
    Sigma(f64),
}

impl TakeForm {
    /// Имя: `1to1` или `t<b>`.
    pub fn label(self) -> String {
        match self {
            Self::OneToOne => "1to1".to_string(),
            Self::Sigma(b) => format!("t{b}"),
        }
    }

    pub fn parse(label: &str) -> anyhow::Result<Self> {
        let form = if label == "1to1" {
            Self::OneToOne
        } else if let Some(rest) = label.strip_prefix('t') {
            Self::Sigma(parse_mult(rest, label)?)
        } else {
            anyhow::bail!("{label}: форма тейка не из базы (1to1|t<b>)")
        };
        anyhow::ensure!(
            form.label() == label,
            "{label}: имя формы тейка не каноническое (ожидалось {})",
            form.label()
        );
        if let Self::Sigma(b) = form {
            anyhow::ensure!(
                b.is_finite() && b >= 0.0,
                "t{b}: множитель σ тейка — конечное число ≥ 0"
            );
        }
        Ok(form)
    }
}

fn parse_mult(rest: &str, label: &str) -> anyhow::Result<f64> {
    rest.parse::<f64>()
        .map_err(|_| anyhow::anyhow!("{label}: число после префикса не разобралось"))
}

/// Форма сделки одной структурой: стоп, тейк и пол σ-тейка в кругах комиссий
/// (`take_floor_fees × ROUNDTRIP_FEES_BPS`; у `1to1` пола нет — как в файлах).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BounceForm {
    pub stop: StopForm,
    pub take: TakeForm,
    pub take_floor_fees: Option<f64>,
}

impl BounceForm {
    /// Сборка из имён CLI с проверкой: σ-тейк требует пол, пол — число > 0.
    pub fn parse(stop: &str, take: &str, take_floor_fees: Option<f64>) -> anyhow::Result<Self> {
        let form = Self {
            stop: StopForm::parse(stop)?,
            take: TakeForm::parse(take)?,
            take_floor_fees,
        };
        form.validate()?;
        Ok(form)
    }

    pub fn validate(self) -> anyhow::Result<()> {
        self.stop.validate()?;
        if let TakeForm::Sigma(b) = self.take {
            anyhow::ensure!(
                b.is_finite() && b >= 0.0,
                "t{b}: множитель σ тейка — конечное число ≥ 0"
            );
        }
        if let Some(k) = self.take_floor_fees {
            anyhow::ensure!(
                k.is_finite() && k > 0.0,
                "--take-floor-fees {k}: пол тейка — конечное число > 0 кругов комиссий"
            );
        }
        if matches!(self.take, TakeForm::Sigma(_)) {
            anyhow::ensure!(
                self.take_floor_fees.is_some(),
                "тейк {} требует --take-floor-fees (пол в кругах комиссий, В-62)",
                self.take.label()
            );
        }
        Ok(())
    }

    /// Нужна ли форме `σ_H` касания.
    pub fn needs_sigma(self) -> bool {
        matches!(self.stop, StopForm::Sigma(_)) || matches!(self.take, TakeForm::Sigma(_))
    }
}

/// Расстояние в bps от цены `entry_tick` (в тиках) — в целых тиках, не
/// меньше одного: `ceil(bps / 10⁴ × entry_tick)`. Округление вверх — чтобы
/// расстояние было **не меньше** заданного (стоп не ближе `a × σ`, тейк не
/// ниже `b × σ`), а цена всегда стояла на сетке тиков.
#[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
fn bps_to_ticks_ceil(bps: f64, entry_tick: i64) -> i64 {
    let ticks = (bps / 10_000.0 * entry_tick as f64).ceil();
    (ticks as i64).max(1)
}

/// Форма сделки, приходящая из CLI (`--post-only`, `--trail-*`, `--grid-*`),
/// одной структурой: у `bounce_plan` иначе стало бы восемь аргументов, а clippy
/// держит предел семи — тот же приём, что у `Session` в `bybit::conn`, где
/// аргументы собраны по смыслу, а не подогнаны под счётчик.
#[derive(Debug, Clone, Copy)]
pub(crate) struct PlanShape {
    pub(crate) post_only: bool,
    pub(crate) trail_bps: f64,
    pub(crate) trail_activate_bps: f64,
    pub(crate) grid_legs: u8,
    pub(crate) grid_step_ticks: i64,
    /// Дедлайн сделки в наносекундах (B3): из предрегистрированной сетки
    /// В-58, приходит из `--deadline-secs`.
    pub(crate) deadline_ns: i64,
    /// Досрочный выход в наносекундах (B4): `0` — выключен, иначе `X` из
    /// набора {1, 2, 3} секунд.
    pub(crate) early_exit_ns: i64,
}

/// План сделки-отскока для касания по форме базы (В-44 → В-62 → В-65):
/// бид-уровень `P` — покупка лимитом от первого фронтранера (иначе `P + 1`
/// тик), стоп по рынку по форме `form.stop`, тейк лимитом по `form.take`;
/// аск зеркально. Расстояния в bps — в целых тиках вверх
/// (`bps_to_ticks_ceil`). Вход снимается в конце касания (`entry_ttl_ns`),
/// позиция закрывается не позже дедлайна. `sigma_bps` — `σ_H` касания для
/// дедлайна формы (`lob::sigma`), нужна только σ-формам. `None` — форму для
/// этого касания не построить (см. `StopForm`); вызывающий считает пропуск.
#[allow(clippy::cast_precision_loss)]
pub(crate) fn bounce_plan(
    touch: &TouchRecord,
    tick: f64,
    form: BounceForm,
    sigma_bps: Option<f64>,
    shape: PlanShape,
) -> Option<(i8, TradePlan)> {
    let PlanShape {
        post_only,
        trail_bps,
        trail_activate_bps,
        grid_legs,
        grid_step_ticks,
        deadline_ns,
        early_exit_ns,
    } = shape;
    let p_tick = touch.price_tick;
    let p = p_tick as f64 * tick;
    let entry_ttl_ns = touch
        .end_ms
        .saturating_sub(touch.start_ms)
        .saturating_mul(1_000_000);
    let grid_step_px = grid_step_ticks as f64 * tick;
    // Знак «в сторону от плотности»: для бида это вверх, для аска — вниз.
    // Стоп, тейк и прежний вход (`P ± 1` тик) считаются по нему.
    let away: i64 = match touch.side {
        Side::Bid => 1,
        Side::Ask => -1,
    };
    // Вход — от **первого фронтранера** касания (B2, В-58; спека §2: «заходят
    // либо в упор, либо от фронтрана» [D 02:35]). Цена фронтрана уже лежит по
    // правильную сторону уровня — для бида выше, для аска ниже, — поэтому знак
    // ей не нужен. Фронтрана впереди не было — прежний `P + 1` тик.
    let entry_tick = touch
        .frontrun_tick
        .unwrap_or_else(|| p_tick.saturating_add(away));
    // Расстояние от уровня «в сторону от плотности», тики (> 0 — по нужную сторону).
    let dist_from_level = (entry_tick - p_tick) * away;
    let stop_tick = match form.stop {
        StopForm::Before => {
            if dist_from_level < 2 {
                return None;
            }
            p_tick + away
        }
        StopForm::At => p_tick,
        StopForm::Behind => p_tick - away,
        StopForm::MidFrontrun => {
            if dist_from_level < 2 {
                return None;
            }
            p_tick + away * (dist_from_level / 2)
        }
        StopForm::BehindStack => touch.stack_next_tick? - away,
        StopForm::Pct(pct) => entry_tick - away * bps_to_ticks_ceil(pct * 100.0, entry_tick),
        StopForm::Sigma(a) => {
            // `a × σ_H` от входа в сторону плотности, но не ближе тика **за**
            // плотностью (`P − 1` для бида): дальний из двух.
            let sigma = sigma_bps?;
            let by_sigma = entry_tick - away * bps_to_ticks_ceil(a * sigma, entry_tick);
            let floor = p_tick - away;
            if (by_sigma - floor) * away > 0 {
                floor
            } else {
                by_sigma
            }
        }
    };
    // Стоп обязан лежать по «свою» сторону от входа — иначе форма вырождена.
    if (entry_tick - stop_tick) * away <= 0 {
        return None;
    }
    let take_tick = match form.take {
        // 1:1 **от входа** (спека §3: «самый базовый, это один к одному» [D 16:17]).
        TakeForm::OneToOne => entry_tick + (entry_tick - stop_tick),
        TakeForm::Sigma(b) => {
            let sigma = sigma_bps?;
            let take_bps = (b * sigma)
                .max(form.take_floor_fees.unwrap_or(0.0) * crate::lob::costs::ROUNDTRIP_FEES_BPS);
            entry_tick + away * bps_to_ticks_ceil(take_bps, entry_tick)
        }
    };
    let entry_px = entry_tick as f64 * tick;
    let stop_px = stop_tick as f64 * tick;
    let take_px = take_tick as f64 * tick;
    let sigma = match touch.side {
        Side::Bid => SIGMA_LONG,
        Side::Ask => SIGMA_SHORT,
    };
    Some((
        sigma,
        TradePlan::Bounce {
            entry_px,
            stop_px,
            take_px,
            deadline_ns,
            entry_ttl_ns,
            post_only,
            trail_bps,
            trail_activate_bps,
            grid_legs,
            grid_step_px,
            early_exit_ns,
            // Уровень касания `P` и шаг цены — данные для решения «уровень ещё
            // держит» (B4): стратегия не знает ни тика, ни цены уровня, они
            // приходят планом, как и всё остальное в нём.
            level_px: p,
            tick_px: tick,
        },
    ))
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

/// Предрегистрированная сетка дедлайнов, секунды (B3, В-58 п. 4): ответ
/// владельца 2 — «S и S-D; S-D значит от секунд до, наверно, пары часов».
const DEADLINES_S: [i64; 4] = [60, 600, 3_600, 7_200];

/// Предрегистрированный набор досрочных выходов, секунды (B4, В-58 п. 5):
/// «прилипание» касания дольше `X`. Отсутствие флага — четвёртый вариант той
/// же оси («выключен»), поэтому `None` здесь не ошибка, а значение сетки.
const EARLY_EXITS_S: [i64; 3] = [1, 2, 3];

/// Дедлайн `--deadline-secs` → наносекунды: значение обязано быть из сетки
/// В-58 (она зафиксирована **до** данных, и «попробовать ещё одно» — отдельное
/// испытание, а не параметр), а сам горизонт — проходить общую проверку
/// `markout::check_horizons` (положительный и не длиннее суток). Вынесено
/// функцией, чтобы отказ проверялся тестом: на живом корне та же ошибка стоит
/// минут счёта до диагностики.
pub(crate) fn deadline_ns_from_secs(secs: i64) -> anyhow::Result<i64> {
    anyhow::ensure!(
        DEADLINES_S.contains(&secs),
        "--deadline-secs {secs} не из предрегистрированной сетки В-58 {DEADLINES_S:?}"
    );
    let ms = secs.saturating_mul(1_000);
    crate::lob::markout::check_horizons(&[ms]).map_err(anyhow::Error::msg)?;
    Ok(ms.saturating_mul(1_000_000))
}

/// Досрочный выход `--early-exit-secs` → наносекунды (B4): `None` — выключен,
/// `Some(X)` — `X` из набора В-58 {1, 2, 3}. Другое значение — отказ, тем же
/// правилом, что у дедлайна.
pub(crate) fn early_exit_ns_from_secs(secs: Option<i64>) -> anyhow::Result<i64> {
    let Some(x) = secs else {
        return Ok(0);
    };
    anyhow::ensure!(
        EARLY_EXITS_S.contains(&x),
        "--early-exit-secs {x} не из предрегистрированного набора В-58 {EARLY_EXITS_S:?}"
    );
    Ok(x.saturating_mul(1_000_000_000))
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
    // Дедлайн и досрочный выход — из предрегистрированных сеток В-58 (B3/B4):
    // другое значение отказ, а не молчаливое расширение сетки.
    let deadline_ns = deadline_ns_from_secs(args.deadline_secs)?;
    let early_exit_ns = early_exit_ns_from_secs(args.early_exit_secs)?;

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
    let h3_lots = mode
        .single_h3_lots()
        .ok_or_else(|| anyhow::anyhow!("режим H3 без единого порога в лотах (notional/strength/both) здесь не поддерживается: оси «×H3» не определены"))?;
    // Форма сделки — из базы (В-65); без имён ветка `--touches` не идёт.
    let form = match (args.stop_form.as_deref(), args.take_form.as_deref()) {
        (Some(stop), Some(take)) => BounceForm::parse(stop, take, args.take_floor_fees)?,
        _ => anyhow::bail!(
            "--touches требует --stop-form и --take-form (формы базы В-65; умолчаний нет)"
        ),
    };
    let replay = super::replay_symbol(&args.session_root, &args.symbol, cfg_levels)?;
    let mut touches: Vec<TouchRecord> = Vec::new();
    let mut mids: Vec<MidSample> = Vec::new();
    for day in &replay.days {
        touches.extend(day.touches.iter().cloned());
        mids.extend(day.mids.iter().copied());
    }
    // Ряд σ — по всей записи символа, чтобы окно раннего касания вторых суток
    // смотрело в первые (`lob::sigma`).
    let sigma_series = crate::lob::sigma::SigmaSeries::from_mids(&mids);
    drop(mids);
    anyhow::ensure!(
        !touches.is_empty(),
        "касаний нет в {}: разметка пуста или порог не дал уровней",
        args.session_root.display()
    );

    // План на каждое касание по форме; касание, для которого форму не
    // построить (нет σ, фронтрана, второй плотности — `bounce_plan` → `None`),
    // выбывает **до** осей и индексов, чтобы круг относился к касанию по тому
    // же порядку, что ниже.
    let deadline_secs = args.deadline_secs;
    let mut skipped = 0usize;
    let (touches, signals): (Vec<TouchRecord>, Vec<BounceSignal>) = touches
        .into_iter()
        .filter_map(|t| {
            let sigma_bps = sigma_series.sigma_bps(t.start_ms, deadline_secs);
            let built = bounce_plan(
                &t,
                tick,
                form,
                sigma_bps,
                PlanShape {
                    post_only: args.post_only,
                    trail_bps: args.trail_bps,
                    trail_activate_bps: args.trail_activate_bps,
                    grid_legs: args.grid_legs,
                    grid_step_ticks: args.grid_step_ticks,
                    deadline_ns,
                    early_exit_ns,
                },
            );
            match built {
                Some((sigma, plan)) => {
                    let signal = BounceSignal {
                        t0_ns: t.start_ms.saturating_mul(1_000_000),
                        sigma,
                        plan,
                        profile: 0,
                    };
                    Some((t, signal))
                }
                None => {
                    skipped += 1;
                    None
                }
            }
        })
        .unzip();
    anyhow::ensure!(
        !touches.is_empty(),
        "ни для одного касания форма {}-{} не строится (пропущено {skipped})",
        form.stop.label(),
        form.take.label()
    );
    let mut order: Vec<usize> = (0..signals.len()).collect();
    order.sort_by_key(|&i| signals[i].t0_ns);

    // Размер круга — явный флаг или `order_size_22a` от пула и цены последнего
    // касания (Decision 22а). Решается здесь, а не в начале: цену даёт реплей
    // касаний, который идёт выше.
    let order_qty_e9 = match (args.order_qty_e9, args.order_qty_from_pool) {
        (Some(v), false) => v,
        (None, true) => {
            let last = touches
                .last()
                .expect("касаний нет — отказ раньше, до сигналов");
            pool_order_qty(
                &args.session_root.join("instruments.csv"),
                &args.symbol,
                last.price_tick,
                tick_e9,
            )?
        }
        (Some(_), true) => anyhow::bail!(
            "--order-qty-e9 и --order-qty-from-pool взаимоисключающие: лот задаётся одним способом"
        ),
        (None, false) => anyhow::bail!(
            "нужен --order-qty-e9 или --order-qty-from-pool: изобретённого умолчания нет (§9 плана)"
        ),
    };
    let order_qty = order_qty_e9 as f64 / 1e9;

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
    // Кривая PnL — такой же артефакт прогона: свой каталог она создаёт сама,
    // как профильный путь и покруговой дамп (иначе прогон в свежий каталог
    // падал бы «путь не найден» уже после записи сводки).
    if let Some(parent) = pnl_out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }

    let header = format!(
        "# lob backtest --touches: {} {} RTT={}нс assumed(В-37), сделка-отскок В-44/В-65 (вход от первого фронтранера, иначе за тик; стоп {}, тейк {}, пол σ-тейка {:?} кругов комиссий, σ_H — за окно дедлайна; дедлайн {} мс из сетки В-58, досрочный выход {}), порог H3={} лотов, касаний {} (форма не построилась у {skipped}), бинлогов {}",
        args.symbol,
        args.session_root.display(),
        args.median_rtt_ns,
        form.stop.label(),
        form.take.label(),
        form.take_floor_fees,
        args.deadline_secs.saturating_mul(1_000),
        match args.early_exit_secs {
            Some(x) => format!("по прилипанию {x} с (сетка В-58)"),
            None => "выключен".to_string(),
        },
        h3_lots,
        touches.len(),
        binlog_paths.len()
    );
    // Шапка артефакта — строкой комментария: чем посчитан файл (`rtt=assumed`
    // В-37, порог `H3`, состав сделки). Пишется до таблицы, как у остальных
    // артефактов вердикта.
    if let Some(trades_out) = args.trades_out.as_ref() {
        write_trades_csv(trades_out, &header, &run)?;
    }
    let mut file = std::fs::File::create(&out)?;
    use std::io::Write as _;
    // `header` уже начинается с `#`: дописывать второй значило бы печатать
    // `# #` в каждом артефакте вердикта.
    writeln!(file, "{header}")?;
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
        "n_early",
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
        let n_early = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Early))
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
            n_early.to_string(),
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
        "bounce: касаний {} · кругов {} · стоп {} · тейк {} · трейл {} · дедлайн {} · досрочно {} · промахи {} · incomplete {}",
        touches.len(),
        run.fills.len(),
        run.exits.stop,
        run.exits.take,
        run.exits.trail,
        run.exits.deadline,
        run.exits.early,
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
