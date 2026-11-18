//! TK-609: таблицы признаков П-42 (поток сделок + производные метрики) — замена `tools/p42/tk594_feat.py`, побитово.
//! `alpha-feat flow|deriv|all --eps <csv> --out <csv> --base-date YYYY-MM-DD [--threads N] [--coins A,B]`;
//! каталоги источников — флагами или переменными `TK594_TRADES`, `TK594_METRICS`, `TK594_PREMIUM`, `TK594_FUNDING_CSV`, `TK594_FUNDING_M`
//! (как у Python); `--store <каталог>` — склад суток TK-607 (`StoreDaySrc`). Последняя строка stdout — JSON итога.
//! TK-611: `alpha-feat f12 --sigsx <side-sigsx.csv> --side long|short --out <csv> [--cache D] [--trades R] [--base-date D] [--threads N] [--coins A,B]` —
//! признаки эпизода f12 (`sigfeat_run.Worker.episode`, U0) по потоку сигналов; кэш минуток и сделки — флагами или `SIGFEAT_CACHE`, `SIGFEAT_TRADES`.

use alpha::breakout::feat::deriv::DerivDirs;
use alpha::breakout::feat::f12::{self, F12Cfg, ZipPxSrc};
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
    /// признаки эпизода f12 (TK-611): уровень, подход, разреженность, корреляции с BTC/ETH
    F12(F12Opts),
}

#[derive(Args)]
struct F12Opts {
    /// `<side>-sigsx.csv` потока кандидатов
    #[arg(long)]
    sigsx: PathBuf,
    /// `long` | `short`
    #[arg(long)]
    side: String,
    /// CSV признаков
    #[arg(long)]
    out: PathBuf,
    /// каталог минуток `<SYM>.b42` (по умолчанию SIGFEAT_CACHE или /data/p42-t566/cache)
    #[arg(long)]
    cache: Option<PathBuf>,
    /// корни aggTrades через `:` (по умолчанию SIGFEAT_TRADES или /data/aggtrades/binance)
    #[arg(long)]
    trades: Option<String>,
    /// дата, от которой отсчитан sig_ms (UTC)
    #[arg(long, default_value = "2026-09-01")]
    base_date: String,
    #[arg(long, default_value_t = 4)]
    threads: usize,
    /// только эти монеты (через запятую)
    #[arg(long)]
    coins: Option<String>,
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
    /// склад суток TK-607 (формат v2, `.tk`): чтение без `unzip`, промах — разбор zip с записью в склад (только flow/all)
    #[arg(long)]
    store: Option<PathBuf>,
    /// потолок объёма склада, ГБ
    #[arg(long, default_value_t = 3)]
    store_cap_gb: u64,
    /// запись в склад прекращается при свободном месте ниже, ГБ
    #[arg(long, default_value_t = 5)]
    store_min_free_gb: u64,
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

fn run_f12(o: F12Opts) -> Result<()> {
    let roots = o
        .trades
        .unwrap_or_else(|| env_or("SIGFEAT_TRADES", "/data/aggtrades/binance"));
    let cfg = F12Cfg {
        sigsx: o.sigsx,
        side: o.side,
        cache: o
            .cache
            .unwrap_or_else(|| env_or("SIGFEAT_CACHE", "/data/p42-t566/cache").into()),
        trades: roots.split(':').map(PathBuf::from).collect(),
        out: o.out,
        base_date: o.base_date,
        threads: o.threads,
        coins: o
            .coins
            .filter(|c| !c.is_empty())
            .map(|c| c.split(',').map(str::to_string).collect()),
    };
    let src = ZipPxSrc {
        roots: cfg.trades.clone(),
    };
    println!("{}", f12::run(&cfg, &src)?.json());
    Ok(())
}

fn main() -> Result<()> {
    let (mode, o) = match Cli::parse().cmd {
        Cmd::Flow(o) => (Mode::Flow, o),
        Cmd::Deriv(o) => (Mode::Deriv, o),
        Cmd::All(o) => (Mode::All, o),
        Cmd::F12(o) => return run_f12(o),
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
        store: o.store.map(|dir| StoreCfg {
            dir,
            cap_gb: o.store_cap_gb,
            min_free_gb: o.store_min_free_gb,
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
