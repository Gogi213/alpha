//! TK-609: таблицы признаков П-42 (поток сделок + производные метрики) — замена `tools/p42/tk594_feat.py`, побитово.
//! `alpha-feat flow|deriv|all --eps <csv> --out <csv> --base-date YYYY-MM-DD [--threads N] [--coins A,B]`;
//! каталоги источников — флагами или переменными `TK594_TRADES`, `TK594_METRICS`, `TK594_PREMIUM`, `TK594_FUNDING_CSV`, `TK594_FUNDING_M`
//! (как у Python); `--store <каталог>` (или `TK594_STORE`, потолок `TK594_STORE_CAP_GB`, свободное место `TK594_STORE_MIN_FREE_GB`) — склад суток TK-607
//! (`StoreDaySrc`): вызывающий код (`rustfeat_run.py` в `retitr_ftab`) аргументов бинарю не добавляет, склад включается окружением. Последняя строка stdout — JSON итога.

use alpha::breakout::feat::deriv::DerivDirs;
use alpha::breakout::feat::io::{run, Cfg, Mode, StoreCfg};
use alpha::breakout::feat::pdcompat::parse_date;
use anyhow::Result;
use clap::{Args, Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "alpha-feat", about = "TK-609: признаки П-42 flow/deriv")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// 16 признаков потока сделок
    Flow(Opts),
    /// 14 производных метрик
    Deriv(Opts),
    /// оба набора
    All(Opts),
}

#[derive(Args)]
struct Opts {
    /// CSV эпизодов: ep_id,sym,side,sig_ms,turn24h
    #[arg(long)]
    eps: PathBuf,
    /// CSV признаков
    #[arg(long)]
    out: PathBuf,
    /// дата, от которой отсчитан sig_ms (UTC)
    #[arg(long, default_value = "2026-09-01")]
    base_date: String,
    #[arg(long, default_value_t = 4)]
    threads: usize,
    /// только эти монеты (через запятую)
    #[arg(long)]
    coins: Option<String>,
    /// корни aggTrades через `:` (по умолчанию TK594_TRADES или /data/aggtrades/binance)
    #[arg(long)]
    trades: Option<String>,
    /// склад суток TK-607 (формат v2, `.tk`): чтение без `unzip`, промах — разбор zip с записью в склад (только flow/all); по умолчанию `TK594_STORE`
    #[arg(long)]
    store: Option<PathBuf>,
    /// потолок объёма склада, ГБ (по умолчанию `TK594_STORE_CAP_GB`, иначе 3; 0 — склад только читается)
    #[arg(long)]
    store_cap_gb: Option<u64>,
    /// запись в склад прекращается при свободном месте ниже, ГБ (по умолчанию `TK594_STORE_MIN_FREE_GB`, иначе 5)
    #[arg(long)]
    store_min_free_gb: Option<u64>,
    /// сверять каждые сутки склада с разбором zip побитово (ворота; вдвое дороже)
    #[arg(long)]
    store_verify: bool,
    #[arg(long)]
    metrics: Option<PathBuf>,
    #[arg(long)]
    premium: Option<PathBuf>,
    #[arg(long)]
    funding_csv: Option<PathBuf>,
    #[arg(long)]
    funding_m: Option<PathBuf>,
}

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn env_u64(name: &str) -> Option<u64> {
    std::env::var(name).ok().and_then(|v| v.trim().parse().ok())
}

fn main() -> Result<()> {
    let (mode, o) = match Cli::parse().cmd {
        Cmd::Flow(o) => (Mode::Flow, o),
        Cmd::Deriv(o) => (Mode::Deriv, o),
        Cmd::All(o) => (Mode::All, o),
    };
    let trades = o
        .trades
        .unwrap_or_else(|| env_or("TK594_TRADES", "/data/aggtrades/binance"));
    let cfg = Cfg {
        mode,
        eps: o.eps,
        out: o.out,
        base_ms: parse_date(&o.base_date)? * 86_400_000,
        threads: o.threads,
        coins: o
            .coins
            .filter(|c| !c.is_empty())
            .map(|c| c.split(',').map(str::to_string).collect()),
        trades: trades.split(':').map(PathBuf::from).collect(),
        store: o
            .store
            .or_else(|| {
                std::env::var("TK594_STORE")
                    .ok()
                    .filter(|d| !d.is_empty())
                    .map(PathBuf::from)
            })
            .map(|dir| StoreCfg {
                dir,
                cap_gb: o
                    .store_cap_gb
                    .or_else(|| env_u64("TK594_STORE_CAP_GB"))
                    .unwrap_or(3),
                min_free_gb: o
                    .store_min_free_gb
                    .or_else(|| env_u64("TK594_STORE_MIN_FREE_GB"))
                    .unwrap_or(5),
                verify: o.store_verify,
            }),
        deriv: DerivDirs {
            metrics: o
                .metrics
                .unwrap_or_else(|| env_or("TK594_METRICS", "/data/p42-metrics").into()),
            premium: o
                .premium
                .unwrap_or_else(|| env_or("TK594_PREMIUM", "/data/p42-premium").into()),
            funding_csv: o
                .funding_csv
                .unwrap_or_else(|| env_or("TK594_FUNDING_CSV", "/data/klines/binance").into()),
            funding_m: o
                .funding_m
                .unwrap_or_else(|| env_or("TK594_FUNDING_M", "/data/p42-metrics/funding").into()),
        },
    };
    let s = run(&cfg)?;
    println!("{}", s.json());
    Ok(())
}
