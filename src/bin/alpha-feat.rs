//! TK-609: таблицы признаков П-42 (поток сделок + производные метрики) — замена `tools/p42/tk594_feat.py`, побитово.
//! `alpha-feat flow|deriv|all --eps <csv> --out <csv> --base-date YYYY-MM-DD [--threads N] [--coins A,B]`;
//! каталоги источников — флагами или переменными `TK594_TRADES`, `TK594_METRICS`, `TK594_PREMIUM`, `TK594_FUNDING_CSV`, `TK594_FUNDING_M`
//! (как у Python). Последняя строка stdout — JSON итога.

use alpha::breakout::feat::deriv::DerivDirs;
use alpha::breakout::feat::io::{run, Cfg, Mode};
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
