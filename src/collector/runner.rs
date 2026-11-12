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
use super::shard::{spawn_shard, ShardHandle, ShardOpts};
use super::writer::day_string;

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum ExchangeName {
    Binance,
    Bybit,
}

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
    #[arg(long, default_value_t = 10)]
    pub top_exclude: usize,
    #[arg(long, default_value_t = LIMIT)]
    pub connect_budget: usize,
    /// Период сброса блока на диск, мс.
    #[arg(long, default_value_t = 200)]
    pub flush_ms: u64,
    /// Пул из файла `pool-*.json` (поле `pool`) вместо REST — тот же набор символов для сравнения.
    #[arg(long)]
    pub pool_file: Option<PathBuf>,
    /// Только шарды с этими именами (через запятую), остальные не запускать — пробы.
    #[arg(long)]
    pub only_shards: Option<String>,
    #[arg(long)]
    pub pool_only: bool,
    /// Остановиться через столько секунд (0 — до сигнала).
    #[arg(long, default_value_t = 0)]
    pub duration_s: u64,
    /// Открытый `ws://host:port` вместо биржи (синтетический источник `collect synth`): хост и порт для всех входов.
    #[arg(long)]
    pub plain_ws: Option<String>,
}

pub fn exchange_of(n: ExchangeName) -> Arc<dyn Exchange> {
    match n {
        ExchangeName::Binance => Arc::new(Binance),
        ExchangeName::Bybit => Arc::new(Bybit),
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

fn load_pool_file(path: &std::path::Path) -> Result<Vec<String>, String> {
    let v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?,
    )
    .map_err(|e| e.to_string())?;
    v.get("pool")
        .and_then(|p| p.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect()
        })
        .ok_or_else(|| "в файле нет поля pool".to_string())
}

pub fn run(args: &RunArgs) -> anyhow::Result<()> {
    let exch = exchange_of(args.exchange);
    std::fs::create_dir_all(&args.root)?;
    let (pool, top) = match &args.pool_file {
        Some(p) => (load_pool_file(p).map_err(anyhow::Error::msg)?, Vec::new()),
        None => exch
            .load_pool(args.min_quote_volume, args.top_exclude)
            .map_err(anyhow::Error::msg)?,
    };
    let run_id = now_ms().div_euclid(1000);
    let pool_json = json!({"ts_ms": now_ms(), "min_quote_volume": args.min_quote_volume, "top_excluded": top, "pool": pool});
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
    let budget = Arc::new(Budget::new(args.connect_budget, WINDOW));
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
    while !stop.load(Relaxed) {
        std::thread::sleep(Duration::from_millis(200));
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
