//! Процесс коллектора: пул биржи, раскладка по шардам, запуск шардов потоками, health раз в период, остановка по сигналу.
//!
//! Один процесс на биржу (шарды — потоки, не процессы: общий бюджет подключений без разделяемой памяти между процессами).
//! Остановка: SIGTERM/SIGINT (повторные игнорируются — очистка не прерывается), `--duration-s` — по таймеру. Порядок: сессии
//! закрываются и пишут `closed`, писатель дочитывает очередь, zstd-кадр закрыт, `.ok` с числом строк, затем выход.

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Arc;
use std::time::{Duration, Instant};

use clap::{Args, ValueEnum};
use serde_json::json;

use super::binance::Binance;
use super::budget::{Budget, LIMIT, WINDOW};
use super::bybit::Bybit;
use super::events::now_ms;
use super::exchange::{Entrance, Exchange, Family};
use super::health::{build, write_atomic, HealthCtx, ProcSampler};
use super::metrics::STATS_PERIOD_S;
use super::okx::Okx;
use super::shard::{spawn_shard, ShardHandle, ShardOpts};
use super::writer::day_string;

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum ExchangeName {
    Binance,
    Bybit,
    Okx,
}

/// Назначаемое (по заданию координатора 12.11): до ~280 из лимита Binance 300 подключений / 5 мин на IP.
const BINANCE_LIMIT: usize = 280;
/// OKX: лимит биржи — 3 подключения в секунду на IP (документация, «Connection limit: 3 requests per second (based on IP)»); 5-минутного окна нет.
/// Назначаемое: 2 в секунду (запас 1 на неточность счёта биржи и ручные пробы с этого IP).
const OKX_LIMIT: usize = 2;
const OKX_WINDOW: Duration = Duration::from_secs(1);

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
    /// Сколько крупнейших по обороту не брать в пул (по умолчанию — правило биржи G-022: Binance и Bybit 10, OKX 0).
    #[arg(long)]
    pub top_exclude: Option<usize>,
    /// Подключений к бирже за 5 мин на процесс (по умолчанию: Binance — 280 из лимита биржи 300 на IP, замер маршрута 12.11: отбраковка медленных
    /// путей съедает до 3 из 4 подключений; Bybit — `LIMIT`; OKX — за 1 с, `OKX_LIMIT`). Отбракованные подключения считаются.
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
    /// Открытый `ws://host:port` вместо биржи (синтетический источник `collect synth`): хост и порт для всех входов.
    #[arg(long)]
    pub plain_ws: Option<String>,
}

pub fn exchange_of(n: ExchangeName) -> Arc<dyn Exchange> {
    match n {
        ExchangeName::Binance => Arc::new(Binance),
        ExchangeName::Bybit => Arc::new(Bybit),
        ExchangeName::Okx => Arc::new(Okx::default()),
    }
}

fn lines_of(exch: &dyn Exchange, args: &RunArgs) -> Result<Vec<Vec<Entrance>>, String> {
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

pub fn run(args: &RunArgs) -> anyhow::Result<()> {
    let exch = exchange_of(args.exchange);
    std::fs::create_dir_all(&args.root)?;
    let (pool, top, extra) = match &args.pool_file {
        Some(p) => {
            let (pool, extra) = load_pool_file(p).map_err(anyhow::Error::msg)?;
            (pool, Vec::new(), Some(extra))
        }
        None => {
            let top_exclude = args.top_exclude.unwrap_or(exch.default_top_exclude());
            let (pool, top) = exch
                .load_pool(args.min_quote_volume, top_exclude)
                .map_err(anyhow::Error::msg)?;
            (pool, top, exch.pool_meta())
        }
    };
    let run_id = now_ms().div_euclid(1000);
    let mut pool_json = json!({"ts_ms": now_ms(), "min_quote_volume": args.min_quote_volume, "top_excluded": top, "pool": pool});
    if let (Some(serde_json::Value::Object(extra)), Some(o)) = (extra, pool_json.as_object_mut()) {
        o.extend(extra);
    }
    std::fs::write(
        args.root.join(format!(
            "pool-{}.json",
            day_string(run_id.div_euclid(86_400))
        )),
        pool_json.to_string(),
    )?;
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
    let lines = lines_of(exch.as_ref(), args).map_err(anyhow::Error::msg)?;
    let mut opts = ShardOpts::new(args.root.clone(), run_id);
    opts.flush = Duration::from_millis(args.flush_ms.max(1));
    opts.max_age = Duration::from_secs(args.max_age_s.max(30));
    opts.never_slow = args.never_slow;
    opts.lazy = args.lazy;
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
    let stop = Arc::new(AtomicBool::new(false));
    watch_signals(&stop);
    let limit = args.connect_budget.unwrap_or(match args.exchange {
        ExchangeName::Binance => BINANCE_LIMIT,
        ExchangeName::Bybit => LIMIT,
        ExchangeName::Okx => OKX_LIMIT,
    });
    let limit = args
        .budget_file
        .as_deref()
        .and_then(read_limit)
        .unwrap_or(limit);
    let window = match args.exchange {
        ExchangeName::Okx => OKX_WINDOW,
        _ => WINDOW,
    };
    let budget = Arc::new(Budget::new(limit, window));
    budget.set_reserve(args.reject_reserve);
    let mut handles: Vec<ShardHandle> = Vec::new();
    for plan in &plans {
        if let Some(h) = spawn_shard(&exch, plan, &lines, &opts, &budget, &stop) {
            handles.push(h);
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
    let ctx = HealthCtx {
        exchange: exch.name(),
        pool: pool.iter().map(|s| s.to_lowercase()).collect(),
        version: option_env!("ALPHA_COMMIT").unwrap_or("unknown").to_string(),
    };
    let metrics: Vec<_> = handles.iter().map(|h| Arc::clone(&h.metrics)).collect();
    let mut sampler = ProcSampler::default();
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
            }
        }
        if deadline.is_some_and(|d| t0.elapsed() >= d) {
            stop.store(true, Relaxed);
        }
        if Instant::now() >= next {
            next += Duration::from_secs(STATS_PERIOD_S);
            let _ = write_atomic(&health_path, &build(&metrics, &ctx, now_ms(), &mut sampler));
        }
    }
    println!("stopping");
    let _ = write_atomic(&health_path, &build(&metrics, &ctx, now_ms(), &mut sampler));
    for h in handles {
        h.join();
    }
    println!("stopped");
    Ok(())
}
