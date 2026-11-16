//! Процесс коллектора: пул биржи, раскладка по шардам, запуск шардов потоками, health раз в период, остановка по сигналу.
//!
//! Один процесс на биржу (шарды — потоки, не процессы: общий бюджет подключений без разделяемой памяти между процессами).
//! Остановка: SIGTERM/SIGINT (повторные игнорируются — очистка не прерывается), `--duration-s` — по таймеру. Порядок: сессии
//! закрываются и пишут `closed`, писатель дочитывает очередь, zstd-кадр закрыт, `.ok` с числом строк, затем выход.

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::mpsc::channel;
use std::sync::Arc;
use std::time::{Duration, Instant};

use clap::{Args, ValueEnum};
use serde_json::json;

use super::backfill;
use super::binance::Binance;
use super::bingx::Bingx;
use super::budget::{Budget, LIMIT, WINDOW};
use super::bybit::Bybit;
use super::events::now_ms;
use super::exchange::{Entrance, Exchange, Family, Turnover};
use super::gate::Gate;
use super::health::{build, write_atomic, HealthCtx, ProcSampler, DEFAULT_BOOK_WINDOW_S};
use super::hostinfo::HostInfo;
use super::livepool::{
    kind_of, spread_plans, Layout, LivePool, Op, PoolFile, PoolState, SavedState, Weights,
    DEFAULT_HOLD_S, DEFAULT_REFRESH_S,
};
use super::metrics::STATS_PERIOD_S;
use super::net;
use super::okx::Okx;
use super::pausecheck;
use super::reactor::{spawn_reactor, spawn_reactor_inline, ReactorCfg};
use super::series::SeriesWriter;
use super::session::StreamSet;
use super::shard::{spawn_shard, ShardHandle, ShardOpts};
use super::storm::{self, Storm};
use super::worker::spawn_worker;

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum ExchangeName {
    Binance,
    Bybit,
    Okx,
    Bingx,
    Gate,
}

/// Назначаемое (по заданию координатора 12.11): до ~280 из лимита Binance 300 подключений / 5 мин на IP.
const BINANCE_LIMIT: usize = 280;
/// OKX: лимит биржи — 3 подключения в секунду на IP (документация, «Connection limit: 3 requests per second (based on IP)»); 5-минутного окна нет.
/// Назначаемое: 2 в секунду (запас 1 на неточность счёта биржи и ручные пробы с этого IP).
const OKX_LIMIT: usize = 2;
const OKX_WINDOW: Duration = Duration::from_secs(1);
/// BingX: числа подключений в секунду/минуту в документации нет (лимит только 60 одновременных соединений с IP); проба 12.11 открывала по 5 сразу без ошибок.
/// Назначаемое: 2 в секунду, как у OKX (полный пул — 30 соединений за ≈ 15 с).
const BINGX_LIMIT: usize = 2;
const BINGX_WINDOW: Duration = Duration::from_secs(1);
/// Gate: в документации — до 300 соединений с IP, числа подключений в секунду нет; замер 15.11: 60 подключений за 0 с и 30 с шагом 30 мс прошли без отказа.
/// Назначаемое: 5 в секунду (полный пул — 18 соединений за ≈ 4 с).
const GATE_LIMIT: usize = 5;
const GATE_WINDOW: Duration = Duration::from_secs(1);
/// Первые минуты после запуска режим «стоянка всей биржи» не оценивается: шарды ещё подключаются.
const STORM_WARMUP: Duration = Duration::from_secs(120);

#[derive(Debug, Args)]
pub struct RunArgs {
    #[arg(long, value_enum)]
    pub exchange: ExchangeName,
    /// Корень данных: `<root>/<день>/<шард>-<запуск>.tsv.zst`, `events-*.jsonl`, `stats-*.jsonl`, `pool-<день>.json`.
    #[arg(long)]
    pub root: PathBuf,
    /// Файл health (по умолчанию `/data/health/<биржа>.json`).
    #[arg(long)]
    pub health: Option<PathBuf>,
    /// Линий на шард: 2 = поток идёт по двум путям сразу (тёплый запас), дубли снимает писатель.
    #[arg(long, default_value_t = 2)]
    pub lanes: usize,
    /// Входы линии по порядку: `host[@ip|@v4|@v6][,запасной,…]`; `-` — по умолчанию биржи.
    #[arg(long)]
    pub entrance: Vec<String>,
    #[arg(long, default_value = "book,trade,mark")]
    pub kinds: String,
    /// Потоков на соединение (0 — по умолчанию вида: Binance книга 32 / сделки 64, Bybit 80).
    #[arg(long, default_value_t = 0)]
    pub streams_per_conn: usize,
    #[arg(long, default_value_t = 300_000.0)]
    pub min_quote_volume: f64,
    /// Сколько крупнейших по обороту не брать в пул (по умолчанию 0: собираем всё, что торгуется выше порога; до 16.11 Binance и Bybit исключали 10).
    #[arg(long)]
    pub top_exclude: Option<usize>,
    /// Период пересмотра пула по REST-обороту, с: новые символы выше порога докладываются в подписку, ушедшие под порог снимаются через `--pool-hold-s`, без перезапуска
    /// (по умолчанию 300; 0 — пул фиксирован на старте, как до 16.11; при `--pool-file` по умолчанию выключен — копии для сравнения идут на том же наборе, включается явным
    /// числом; при `--only-shards` выключен всегда).
    #[arg(long)]
    pub pool_refresh_s: Option<u64>,
    /// Гистерезис выхода из пула, с: символ остаётся в подписке, пока его оборот ниже порога не держится столько секунд подряд (по умолчанию сутки).
    #[arg(long, default_value_t = DEFAULT_HOLD_S)]
    pub pool_hold_s: u64,
    /// Окно независимого счётчика «не подписано», с: символ из состава REST без кадра книги за это время — не подписан (по умолчанию 600; страница здоровья красит по нему,
    /// а не по «данные за минуту»: тихие символы молчат по минуте и дольше без потери данных).
    #[arg(long, default_value_t = DEFAULT_BOOK_WINDOW_S)]
    pub book_window_s: u64,
    /// Подключений к бирже за 5 мин на процесс (по умолчанию: Binance — 280 из лимита биржи 300 на IP, замер маршрута 12.11: отбраковка медленных
    /// путей съедает до 3 из 4 подключений; Bybit — `LIMIT`; OKX — за 1 с, `OKX_LIMIT`; BingX — за 1 с, `BINGX_LIMIT`; Gate — за 1 с, `GATE_LIMIT`). Отбракованные подключения считаются.
    #[arg(long)]
    pub connect_budget: Option<usize>,
    /// Файл с лимитом подключений за 5 мин (одно число), перечитывается раз в 2 с и заменяет `--connect-budget`. Передача между двумя процессами
    /// одной биржи на одном IP: сумма их лимитов не больше лимита биржи (Binance 300) — напр. 140 + 140 на время перекрытия, после остановки
    /// старого — 280 новому; каждый процесс считает свои подключения, поэтому вместе они в любом окне в пределах суммы.
    #[arg(long)]
    pub budget_file: Option<PathBuf>,
    /// Места бюджета подключений, которые отбраковка пути по RTT не трогает. По умолчанию 0 — как в Python (отбраковка до исчерпания бюджета,
    /// дальше подключение ждёт места): запас 2 × соединений при замере 12.11 13:23 UTC (маршрут до Binance 350–380 мс) после старта оставил
    /// отбраковку выключенной, и 164 из 200 новых подключений приняли медленный путь (лаг книги p99 66 с).
    #[arg(long, default_value_t = 0)]
    pub reject_reserve: usize,
    /// Период сброса блока на диск, мс.
    #[arg(long, default_value_t = 100)]
    pub flush_ms: u64,
    /// Пул из файла `pool-*.json` (поле `pool`) вместо REST — тот же набор символов для сравнения.
    #[arg(long)]
    pub pool_file: Option<PathBuf>,
    /// Только шарды с этими именами (через запятую), остальные не запускать — пробы.
    #[arg(long)]
    pub only_shards: Option<String>,
    #[arg(long)]
    pub pool_only: bool,
    /// Возраст соединения, после которого линия открывает замену (по умолчанию 23 ч: биржа рвёт сутки). Короткий — для проверки замены вживую.
    #[arg(long, default_value_t = 23 * 3600)]
    pub max_age_s: u64,
    /// Остановиться через столько секунд (0 — до сигнала).
    #[arg(long, default_value_t = 0)]
    pub duration_s: u64,
    /// Замена испорченного пути не принимает медленный путь «как есть» после 12 отбраковок: старая сессия остаётся, пока не найден быстрый путь.
    #[arg(long)]
    pub never_slow: bool,
    /// Ленивая замена: линия не заменяется, пока партнёр по шарду здоров; возрастная пересменка не откладывается.
    #[arg(long)]
    pub lazy: bool,
    /// Шард без соединения проверяет путь, как Python: отбраковка по RTT (до 12 раз) и окно лага до приёма, вместо приёма сразу.
    #[arg(long)]
    pub cold_probe: bool,
    /// Разрешить IPv6 во входах (по умолчанию IPv6 не используется нигде: ни основным, ни запасным входом; 13.11, таймауты на IPv6-путях).
    #[arg(long)]
    pub allow_ipv6: bool,
    /// Период пробуждений рабочего процесса, мкс: кадры не будят писателей, рабочий раз в такт забирает очереди всех шардов (collector-perf: 1 мс).
    #[arg(long, default_value_t = 1000)]
    pub worker_tick_us: u64,
    /// Реактор приёма: один поток читает все соединения процесса (epoll), вместо потока на соединение (collector-perf).
    #[arg(long)]
    pub reactor: bool,
    /// Окно пакетирования реактора, мкс: реактор просыпается не чаще раза за окно.
    #[arg(long, default_value_t = 1000)]
    pub reactor_window_us: u64,
    /// Рабочий (разбор, дубли, кодировщик) идёт внутри реактора, в его потоке, а не в отдельном (нужен `--reactor`): нет передачи между потоками и пробуждений рабочего.
    #[arg(long)]
    pub worker_inline: bool,
    /// Метка `rx` в файле: `user` — после пробуждения потока (как раньше), `kernel` — ядерная метка приёма (нужен `--reactor`).
    #[arg(long, default_value = "user")]
    pub rx_stamp: String,
    /// Нет pong на наш пинг при свежих данных (последний кадр моложе 2 с) не портит линию — журнал и счётчик без замены (см. `Shared::pong_only_degrades`); по умолчанию портит, как раньше.
    #[arg(long)]
    pub no_pong_only_degrade: bool,
    /// Не слать наш протокольный пинг (опыт; боевое поведение — пинг раз в 5 с).
    #[arg(long)]
    pub no_ping: bool,
    /// Подписка из реактора (нужен `--reactor`): сообщения подписки уходят в его цикле с паузой биржи, а соединение читается с первого кадра — нет очереди ядра на время подписки
    /// (BingX: 179 тем × 20 мс = 3,7 с, кадры первых тем приходили к писателю с ядерной меткой давностью секунд и поднятием `rx`).
    #[arg(long)]
    pub sub_in_reactor: bool,
    /// Выдержка записей перед записью, мс (только при ядерных метках): пачка сортируется по `rx` и выпускается, когда метка старше выдержки, — запись, опоздавшая в писатель
    /// не больше чем на неё, сохраняет настоящую метку вместо поднятия до метки предыдущей (`rx_clamped`). 0 — выключено.
    #[arg(long, default_value_t = 0)]
    pub rx_hold_ms: u64,
    /// Локальный TLS-источник `wss://host:port` вместо биржи (замер ошибки метки приёма; корень — `ALPHA_TLS_EXTRA_ROOT`): хост и порт для всех входов.
    #[arg(long)]
    pub tls_ws: Option<String>,
    /// Открытый `ws://host:port` вместо биржи (синтетический источник `collect synth`): хост и порт для всех входов.
    #[arg(long)]
    pub plain_ws: Option<String>,
}

pub fn exchange_of(n: ExchangeName) -> Arc<dyn Exchange> {
    match n {
        ExchangeName::Binance => Arc::new(Binance),
        ExchangeName::Bybit => Arc::new(Bybit),
        ExchangeName::Okx => Arc::new(Okx::default()),
        ExchangeName::Bingx => Arc::new(Bingx::default()),
        ExchangeName::Gate => Arc::new(Gate::default()),
    }
}

/// Вход IPv6: семейство `V6` или закреплённый адрес IPv6.
fn is_v6(e: &Entrance) -> bool {
    e.family == Family::V6 || e.ip.is_some_and(|ip| ip.is_ipv6())
}

pub(super) fn lines_of(exch: &dyn Exchange, args: &RunArgs) -> Result<Vec<Vec<Entrance>>, String> {
    let mut lines = exch.default_entrances(args.lanes);
    let default_family = lines
        .first()
        .and_then(|l| l.first())
        .map_or(Family::Any, |e| e.family);
    for (k, spec) in args.entrance.iter().enumerate() {
        if spec == "-" {
            continue;
        }
        let list = spec
            .split(',')
            .map(|x| Entrance::parse(x, default_family))
            .collect::<Result<Vec<_>, _>>()?;
        if let Some(l) = lines.get_mut(k) {
            *l = list;
        }
    }
    if !args.allow_ipv6 {
        for (k, l) in lines.iter_mut().enumerate() {
            l.retain(|e| !is_v6(e));
            if l.is_empty() {
                return Err(format!(
                    "линия {k}: остались только входы IPv6 — нужен --allow-ipv6"
                ));
            }
        }
    }
    Ok(lines)
}

/// Остановка по сигналу: поток слушает SIGTERM/SIGINT и ставит флаг; повторные сигналы флаг не меняют и процесс не рвут.
fn watch_signals(stop: &Arc<AtomicBool>) {
    let stop = Arc::clone(stop);
    let _ = std::thread::Builder::new()
        .name("signals".into())
        .spawn(move || {
            let Ok(rt) = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            else {
                return;
            };
            rt.block_on(async {
                #[cfg(unix)]
                {
                    use tokio::signal::unix::{signal, SignalKind};
                    let (Ok(mut term), Ok(mut int)) = (
                        signal(SignalKind::terminate()),
                        signal(SignalKind::interrupt()),
                    ) else {
                        return;
                    };
                    loop {
                        tokio::select! {
                            _ = term.recv() => {}
                            _ = int.recv() => {}
                        }
                        stop.store(true, Relaxed);
                    }
                }
                #[cfg(not(unix))]
                {
                    loop {
                        let _ = tokio::signal::ctrl_c().await;
                        stop.store(true, Relaxed);
                    }
                }
            });
        });
}

/// Лимит из файла: одно положительное число; нет файла или не число — `None` (лимит прежний).
fn read_limit(path: &std::path::Path) -> Option<usize> {
    std::fs::read_to_string(path)
        .ok()?
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|n| *n > 0)
}

/// Пул из файла и его `meta`/`qv` (если есть) — они переходят в `pool-<день>.json` процесса.
fn load_pool_file(path: &std::path::Path) -> Result<(Vec<String>, serde_json::Value), String> {
    let v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?,
    )
    .map_err(|e| e.to_string())?;
    let pool = v
        .get("pool")
        .and_then(|p| p.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect()
        })
        .ok_or_else(|| "в файле нет поля pool".to_string())?;
    let extra: serde_json::Map<String, serde_json::Value> = ["meta", "qv"]
        .iter()
        .filter_map(|k| Some(((*k).to_string(), v.get(*k)?.clone())))
        .collect();
    Ok((pool, serde_json::Value::Object(extra)))
}

/// Разделы health, которые собирает процесс, а не шарды: бюджет подключений, режим «стоянка всей биржи», дозапрос сделок.
struct Extras<'a> {
    budget: &'a Budget,
    storm: &'a Storm,
    backfill: Option<&'a backfill::Backfill>,
    pause_check: Option<&'a pausecheck::PauseCheck>,
}

/// health.json процесса: раздел шардов + (если есть реактор) раздел `reactor`.
fn health_json(
    metrics: &[Arc<super::metrics::ShardMetrics>],
    ctx: &HealthCtx,
    sampler: &mut ProcSampler,
    reactor: Option<&(super::reactor::ReactorHandle, std::thread::JoinHandle<()>)>,
    host: &mut HostInfo,
    extras: &Extras<'_>,
) -> serde_json::Value {
    let Extras {
        budget,
        storm,
        backfill,
        pause_check,
    } = *extras;
    host.refresh();
    let mut v = build(metrics, ctx, now_ms(), sampler);
    if let Some(o) = v.as_object_mut() {
        o.insert("host".to_string(), host.json());
        // тревога «бюджет подключений исчерпан» и режим «стоянка всей биржи» — на страницу здоровья
        o.insert(
            "connect_budget".to_string(),
            budget.snapshot(Instant::now()).json(),
        );
        o.insert("storm".to_string(), storm.json(now_ms()));
        o.insert(
            "backfill".to_string(),
            backfill.map_or_else(
                || json!({"enabled": false}),
                |b| b.stats.json(true, now_ms()),
            ),
        );
        // паузы шардов сделок проверяет REST: тишина рынка не красит, потеря и «не проверено» красят; идущая пауза — самая долгая, с
        let now = now_ms();
        let ongoing_s = metrics
            .iter()
            .filter(|m| super::livepool::kind_of(&m.name) == "trade")
            .map(|m| m.gap_since_ms.load(std::sync::atomic::Ordering::Relaxed))
            .filter(|&t| t > 0)
            .map(|t| u64::try_from((now - t).max(0) / 1000).unwrap_or(0))
            .max()
            .unwrap_or(0);
        o.insert(
            "pause_check".to_string(),
            pause_check.map_or_else(
                || json!({"enabled": false}),
                |p| p.stats.json(now, ongoing_s),
            ),
        );
    }
    if let (Some((r, _)), Some(o)) = (reactor, v.as_object_mut()) {
        o.insert("reactor".to_string(), r.stats.json());
    }
    if let Some(o) = v.as_object_mut() {
        o.insert("gzip".to_string(), super::gzip::stats_json());
    }
    v
}

/// Места бюджета, которые занимают только шарды без соединения: по два на шард (обе линии), не больше трети лимита. В правилах с окном в секунды резерв не нужен.
fn urgent_reserve(limit: usize, shards: usize, window: Duration) -> usize {
    if window < Duration::from_secs(60) {
        0
    } else {
        shards.saturating_mul(2).min(limit / 3)
    }
}

/// Счётчики кадров по символам со всех шардов: (вид шарда, символ в нижнем регистре, кадров с начала процесса) — вход измерения нагрузки (`LivePool::observe`).
fn frame_counts(metrics: &[Arc<super::metrics::ShardMetrics>]) -> Vec<(String, String, u64)> {
    let mut v = Vec::new();
    for m in metrics {
        let kind = kind_of(&m.name).to_string();
        let w = m.writer();
        v.extend(
            w.seen
                .values()
                .map(|(low, _, n)| (kind.clone(), low.clone(), *n)),
        );
    }
    v
}

pub fn run(args: &RunArgs) -> anyhow::Result<()> {
    net::set_allow_ipv6(args.allow_ipv6);
    let exch = exchange_of(args.exchange);
    std::fs::create_dir_all(&args.root)?;
    let top_exclude = args.top_exclude.unwrap_or(exch.default_top_exclude());
    let (pool, top, extra) = match &args.pool_file {
        Some(p) => {
            let (pool, extra) = load_pool_file(p).map_err(anyhow::Error::msg)?;
            (pool, Vec::new(), Some(extra))
        }
        None => {
            let (pool, top) = exch
                .load_pool(args.min_quote_volume, top_exclude)
                .map_err(anyhow::Error::msg)?;
            (pool, top, exch.pool_meta())
        }
    };
    let run_id = now_ms().div_euclid(1000);
    // пул запуска — свой файл `pool-<день>-<запуск>.json` с метками времени; прежний `pool-<день>.json` пишется тем же документом для читателей по имени
    let refresh_s = args.pool_refresh_s.unwrap_or(if args.pool_file.is_some() {
        0
    } else {
        DEFAULT_REFRESH_S
    });
    let live_on = refresh_s > 0 && args.only_shards.is_none();
    // перезапуск не сокращает выдержку: символы прежнего запуска, что держались под порогом меньше `--pool-hold-s`, возвращаются в пул с прежними метками;
    // оборот REST на старте — и проверка, что контракт ещё торгуется, и оценка нагрузки для раскладки
    let mut pool = pool;
    let mut held: std::collections::BTreeMap<String, i64> = std::collections::BTreeMap::new();
    let mut start_qv: std::collections::BTreeMap<String, f64> = std::collections::BTreeMap::new();
    if live_on {
        match exch.load_turnover() {
            Ok(t) => {
                if let Some(saved) = SavedState::load(&args.root) {
                    held = saved.held(&pool, now_ms(), args.pool_hold_s, &t.qv);
                    if !held.is_empty() {
                        pool.extend(held.keys().cloned());
                        pool.sort();
                    }
                }
                start_qv = t.qv;
            }
            Err(e) => {
                println!("start turnover failed (held symbols and load weights skipped): {e}")
            }
        }
    }
    let mut pool_json = json!({"ts_ms": now_ms(), "min_quote_volume": args.min_quote_volume, "top_excluded": top, "pool": pool,
                               "held_from_previous_run": held.keys().collect::<Vec<_>>()});
    if let (Some(serde_json::Value::Object(extra)), Some(o)) = (extra, pool_json.as_object_mut()) {
        o.extend(extra);
    }
    let pool_file = PoolFile::create(
        &args.root,
        run_id,
        pool_json,
        if live_on { refresh_s } else { 0 },
        args.pool_hold_s,
    )?;
    // источник метки приёма `rx` — по запускам (дописывается): разрыв семантики в непрерывном наборе данных виден по датам (docs/collector-ws-format.md)
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(args.root.join("rx-stamp.jsonl"))
    {
        use std::io::Write as _;
        let _ = writeln!(
            f,
            "{}",
            json!({"ts_ms": now_ms(), "version": super::VERSION, "rx_stamp": if args.reactor && args.rx_stamp == "kernel" { "kernel" } else { "user" },
                   // режим ядерной метки: `exact` — метка у каждой записи TLS (`MSG_PEEK` + `recvmsg` на запись, так работает `--rx-stamp kernel`), иначе `none` (пользовательская метка)
                   "mode": if args.reactor && args.rx_stamp == "kernel" { "exact" } else { "none" },
                   "reactor": args.reactor, "worker_inline": args.worker_inline, "window_us": args.reactor_window_us,
                   "sub_in_reactor": args.sub_in_reactor, "rx_hold_ms": args.rx_hold_ms})
        );
    }
    println!("pool {} top_excluded {:?}", pool.len(), top);
    if args.pool_only {
        return Ok(());
    }
    let kinds: Vec<String> = args.kinds.split(',').map(str::to_string).collect();
    let mut plans = exch.plan(&pool, &kinds, args.streams_per_conn);
    if let Some(only) = &args.only_shards {
        let keep: HashSet<&str> = only.split(',').collect();
        plans.retain(|p| keep.contains(p.name.as_str()));
    }
    let mut live = live_on.then(|| {
        // состояние прежнего запуска: метки выдержки и измеренная нагрузка; оборот REST — для оценки нагрузки символов без измерений
        let saved = SavedState::load(&args.root).unwrap_or_default();
        let weights = Weights::new(saved.rates.clone(), &start_qv);
        let mut layout_plans = plans.clone();
        if weights.informative() {
            layout_plans = spread_plans(
                exch.as_ref(),
                &plans,
                &pool,
                &kinds,
                args.streams_per_conn,
                &weights,
            );
            plans = layout_plans.clone();
        }
        let mut layout = Layout::new(
            exch.as_ref(),
            &layout_plans,
            &pool,
            &kinds,
            args.streams_per_conn,
        );
        layout.set_weights(weights);
        LivePool::new(
            Arc::clone(&exch),
            PoolState::seeded(
                &pool,
                now_ms(),
                args.min_quote_volume,
                top_exclude,
                args.pool_hold_s,
                &held,
            ),
            layout,
            Some(pool_file),
            refresh_s,
            args.pool_hold_s,
        )
        .with_state(args.root.clone(), saved.rates)
    });
    let lines = lines_of(exch.as_ref(), args).map_err(anyhow::Error::msg)?;
    let mut opts = ShardOpts::new(args.root.clone(), run_id);
    opts.flush = Duration::from_millis(args.flush_ms.max(1));
    opts.max_age = Duration::from_secs(args.max_age_s.max(30));
    opts.never_slow = args.never_slow;
    opts.lazy = args.lazy;
    let storm = Storm::new();
    opts.storm = Some(Arc::clone(&storm));

    opts.pong_only_degrades = !args.no_pong_only_degrade;
    opts.no_ping = args.no_ping;
    opts.cold_probe = args.cold_probe;
    let mut lines = lines;
    if let Some(plain) = &args.plain_ws {
        let (host, port) = plain
            .rsplit_once(':')
            .ok_or_else(|| anyhow::anyhow!("--plain-ws host:port"))?;
        opts.tls = false;
        opts.port = port.parse()?;
        lines = (0..args.lanes)
            .map(|_| vec![Entrance::new(host, Family::V4)])
            .collect();
    }
    if let Some(t) = &args.tls_ws {
        let (host, port) = t
            .rsplit_once(':')
            .ok_or_else(|| anyhow::anyhow!("--tls-ws host:port"))?;
        opts.port = port.parse()?;
        lines = (0..args.lanes)
            .map(|_| vec![Entrance::new(host, Family::V4)])
            .collect();
    }
    let stop = Arc::new(AtomicBool::new(false));
    watch_signals(&stop);
    // дозапрос потерянных сделок: биржи со сплошным номером сделки (Binance, OKX, BingX, Gate); отдельный поток, отдельный файл `backfill-*.tsv.zst`
    let backfill = backfill::spawn(
        Arc::clone(&exch),
        args.root.clone(),
        run_id,
        Arc::clone(&stop),
    );
    opts.backfill = backfill.as_ref().map(|(b, _)| b.clone());
    // проверка пауз шардов сделок по REST: биржи без номера сделки (Bybit); отдельный поток
    let pause_check = pausecheck::spawn(Arc::clone(&exch), Arc::clone(&stop));
    opts.pause_check = pause_check.as_ref().map(|(p, _)| p.clone());
    let limit = args.connect_budget.unwrap_or(match args.exchange {
        ExchangeName::Binance => BINANCE_LIMIT,
        ExchangeName::Bybit => LIMIT,
        ExchangeName::Okx => OKX_LIMIT,
        ExchangeName::Bingx => BINGX_LIMIT,
        ExchangeName::Gate => GATE_LIMIT,
    });
    let limit = args
        .budget_file
        .as_deref()
        .and_then(read_limit)
        .unwrap_or(limit);
    let window = match args.exchange {
        ExchangeName::Okx => OKX_WINDOW,
        ExchangeName::Bingx => BINGX_WINDOW,
        ExchangeName::Gate => GATE_WINDOW,
        _ => WINDOW,
    };
    let budget = Arc::new(Budget::new(limit, window));
    budget.set_reserve(args.reject_reserve);
    budget.set_urgent_reserve(urgent_reserve(limit, plans.len(), window));
    let tick = Duration::from_micros(args.worker_tick_us.clamp(100, 100_000));
    opts.worker_tick = tick;
    let kernel_stamp = args.reactor && args.rx_stamp == "kernel";
    opts.kernel_stamp = kernel_stamp;
    opts.rx_hold = if kernel_stamp {
        Duration::from_millis(args.rx_hold_ms.min(5_000))
    } else {
        Duration::ZERO
    };
    let rcfg = ReactorCfg {
        window: Duration::from_micros(args.reactor_window_us.min(100_000)),
        kernel_stamp,
        pace_sub: args.sub_in_reactor,
    };
    let mut worker_pair = None;
    let reactor = if args.reactor && args.worker_inline {
        spawn_reactor_inline(rcfg).map(|(r, w, j)| {
            opts.worker = Some(w.clone());
            worker_pair = Some((w, None));
            (r, j)
        })
    } else if args.reactor {
        spawn_reactor(rcfg)
    } else {
        None
    };
    if worker_pair.is_none() {
        let (w, j) = spawn_worker(tick);
        opts.worker = Some(w.clone());
        worker_pair = Some((w, Some(j)));
    }
    opts.reactor = reactor.as_ref().map(|(h, _)| h.clone());
    // Binance: кадр mark — массив ≈ 1000 элементов (82 КБ, разбор ≈ 2 мс на горячем ядре, 2 раза в секунду): в потоке реактора он задерживал бы разбор кадров всех
    // остальных шардов (голова очереди, `queue_lag` p99 26–34 мс против 8 мс у прежней сборки), поэтому шарды mark идут через отдельный поток рабочего
    let mark_worker =
        (args.reactor && args.worker_inline && matches!(args.exchange, ExchangeName::Binance))
            .then(|| {
                spawn_worker(Duration::from_micros(
                    args.reactor_window_us.clamp(2_000, 100_000),
                ))
            });
    let mut handles: Vec<ShardHandle> = Vec::new();
    // живые наборы потоков по индексу шарда раскладки (`None` — шард не запустился)
    let mut stream_sets: Vec<Option<Arc<StreamSet>>> = Vec::new();
    for plan in &plans {
        let mut o = opts.clone();
        if let (true, Some((w, _))) = (plan.name.starts_with("mark"), mark_worker.as_ref()) {
            o.worker = Some(w.clone());
            o.worker_tick = Duration::from_micros(args.reactor_window_us.clamp(2_000, 100_000));
        }
        match spawn_shard(&exch, plan, &lines, &o, &budget, &stop) {
            Some(h) => {
                stream_sets.push(Some(Arc::clone(&h.streams)));
                handles.push(h);
            }
            None => stream_sets.push(None),
        }
    }
    println!(
        "shards {}: {}",
        handles.len(),
        plans
            .iter()
            .map(|p| format!("{}={}", p.name, p.streams.len()))
            .collect::<Vec<_>>()
            .join(", ")
    );
    let health_path = args
        .health
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("/data/health/{}.json", exch.name())));
    let mut ctx = HealthCtx {
        exchange: exch.name(),
        pool: pool.iter().map(|s| s.to_lowercase()).collect(),
        version: super::VERSION.to_string(),
        live: live.as_ref().map(LivePool::health),
        book_window_ms: i64::try_from(args.book_window_s.max(60).saturating_mul(1000))
            .unwrap_or(i64::MAX),
    };
    let mut metrics: Vec<_> = handles.iter().map(|h| Arc::clone(&h.metrics)).collect();
    // REST-опрос оборота — отдельный поток (запрос блокирующий, до 30 с): основной цикл не ждёт, результат приходит по каналу
    let (refresh_tx, refresh_rx) = channel::<Result<Turnover, String>>();
    if live.is_some() {
        let (exch2, stop2) = (Arc::clone(&exch), Arc::clone(&stop));
        let period = Duration::from_secs(refresh_s);
        let first = period.min(Duration::from_secs(60));
        let _ = std::thread::Builder::new()
            .name("pool-refresh".into())
            .spawn(move || {
                let mut next = Instant::now() + first;
                while !stop2.load(Relaxed) {
                    std::thread::sleep(Duration::from_millis(200));
                    if Instant::now() < next {
                        continue;
                    }
                    let r = exch2.load_turnover();
                    next = Instant::now() + period;
                    if refresh_tx.send(r).is_err() {
                        return;
                    }
                }
            });
    }
    let mut sampler = ProcSampler::default();
    let mut host = HostInfo::new();
    let mut series = SeriesWriter::open(&args.root, now_ms(), super::VERSION);
    if let Some((b, _)) = &backfill {
        series.set_backfill(Arc::clone(&b.stats));
    }
    if let Some((p, _)) = &pause_check {
        series.set_pause_check(Arc::clone(&p.stats));
    }
    let (t0, deadline) = (
        Instant::now(),
        (args.duration_s > 0).then(|| Duration::from_secs(args.duration_s)),
    );
    let mut next = Instant::now() + Duration::from_secs(STATS_PERIOD_S);
    let (mut next_limit, mut applied) = (Instant::now(), limit);
    while !stop.load(Relaxed) {
        std::thread::sleep(Duration::from_millis(200));
        if let (Some(f), true) = (&args.budget_file, Instant::now() >= next_limit) {
            next_limit = Instant::now() + Duration::from_secs(2);
            if let Some(n) = read_limit(f).filter(|n| *n != applied) {
                budget.set_limit(n);
                println!("connect budget {applied} -> {n}");
                applied = n;
                budget.set_urgent_reserve(urgent_reserve(n, stream_sets.len(), window));
            }
        }
        if deadline.is_some_and(|d| t0.elapsed() >= d) {
            stop.store(true, Relaxed);
        }
        // «стоянка всей биржи»: шард испорчен, если стоят данные, линия испорчена или соединений нет; первые 2 мин после запуска не оцениваются (идёт подключение)
        if t0.elapsed() >= STORM_WARMUP {
            let bad = metrics
                .iter()
                .filter(|m| m.connected.load(Relaxed) <= 0 || storm::impaired(m))
                .count();
            storm.evaluate(bad, metrics.len(), now_ms());
        }
        while let Ok(msg) = refresh_rx.try_recv() {
            let Some(lp) = live.as_mut() else { continue };
            let now = now_ms();
            match msg {
                Err(e) => {
                    lp.failed(now, &e);
                    println!("pool refresh failed: {e}");
                }
                Ok(t) => {
                    let counts = frame_counts(&metrics);
                    lp.observe(
                        now,
                        counts.iter().map(|(k, s, n)| (k.as_str(), s.as_str(), *n)),
                    );
                    let review = lp.apply(&t, now);
                    for op in review.ops {
                        match op {
                            Op::Append { slot, streams } => {
                                if let Some(Some(set)) = stream_sets.get(slot) {
                                    set.apply(&streams, &[]);
                                }
                            }
                            Op::Remove { slot, streams } => {
                                if let Some(Some(set)) = stream_sets.get(slot) {
                                    set.apply(&[], &streams);
                                }
                            }
                            Op::Spawn { slot, plan } => {
                                if slot != stream_sets.len() {
                                    continue;
                                }
                                match spawn_shard(&exch, &plan, &lines, &opts, &budget, &stop) {
                                    Some(h) => {
                                        println!(
                                            "pool: new shard {} ({} streams)",
                                            plan.name,
                                            plan.streams.len()
                                        );
                                        metrics.push(Arc::clone(&h.metrics));
                                        stream_sets.push(Some(Arc::clone(&h.streams)));
                                        handles.push(h);
                                    }
                                    None => stream_sets.push(None),
                                }
                            }
                        }
                    }
                    if !review.changes.is_empty() {
                        let a = review.changes.iter().filter(|c| c.add).count();
                        println!(
                            "pool refresh: +{a} -{} -> {} symbols, {} shards",
                            review.changes.len() - a,
                            lp.state.len(),
                            stream_sets.len()
                        );
                    }
                    ctx.pool = lp.lower_pool();
                    budget.set_urgent_reserve(urgent_reserve(applied, stream_sets.len(), window));
                }
            }
            ctx.live = Some(lp.health());
        }
        if Instant::now() >= next {
            next += Duration::from_secs(STATS_PERIOD_S);
            let _ = write_atomic(
                &health_path,
                &health_json(
                    &metrics,
                    &ctx,
                    &mut sampler,
                    reactor.as_ref(),
                    &mut host,
                    &Extras {
                        budget: &budget,
                        storm: &storm,
                        backfill: backfill.as_ref().map(|(b, _)| b),
                        pause_check: pause_check.as_ref().map(|(p, _)| p),
                    },
                ),
            );
            series.flush(&metrics, now_ms());
        }
    }
    println!("stopping");
    let _ = write_atomic(
        &health_path,
        &health_json(
            &metrics,
            &ctx,
            &mut sampler,
            reactor.as_ref(),
            &mut host,
            &Extras {
                budget: &budget,
                storm: &storm,
                backfill: backfill.as_ref().map(|(b, _)| b),
                pause_check: pause_check.as_ref().map(|(p, _)| p),
            },
        ),
    );
    series.flush(&metrics, now_ms());
    for h in handles {
        h.join();
    }
    if let Some((_, j)) = backfill {
        let _ = j.join();
    }
    if let Some((_, j)) = pause_check {
        let _ = j.join();
    }
    if let Some((w, j)) = mark_worker {
        w.quit();
        let _ = j.join();
    }
    // рабочий в своём потоке — остановить и дождаться; рабочий в реакторе — команда остановки доходит вместе с остановкой реактора
    if let Some((w, join)) = worker_pair {
        w.quit();
        if let Some(j) = join {
            let _ = j.join();
        }
    }
    if let Some((r, j)) = reactor {
        r.quit();
        let _ = j.join();
    }
    println!("stopped");
    Ok(())
}
