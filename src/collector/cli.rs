//! Подкоманды `alpha collect …`.

use clap::Subcommand;

use super::bench::{run as bench_run, BenchArgs};
use super::ladder::{run as ladder_run, LadderArgs};
use super::runner::{run, RunArgs};
use super::synth::{run as synth_run, SynthArgs};

#[derive(Debug, Subcommand)]
pub enum CollectCommand {
    /// Коллектор биржи: приём по WebSocket, запись суточных файлов, health.
    Run(RunArgs),
    /// Синтетический источник кадров Binance (локальный ws://) для лестницы нагрузки.
    Synth(SynthArgs),
    /// Лестница нагрузки Binance на Rust: одно соединение, n потоков одного вида, окно (как `tools/collector/ladder.py`).
    Ladder(LadderArgs),
    /// Замеры на малом: разбор, кодировщик, очередь, zstd (выбор архитектуры).
    Bench(BenchArgs),
}

pub fn dispatch(cmd: CollectCommand) -> anyhow::Result<()> {
    match cmd {
        CollectCommand::Run(a) => run(&a),
        CollectCommand::Synth(a) => synth_run(&a),
        CollectCommand::Ladder(a) => ladder_run(&a),
        CollectCommand::Bench(a) => {
            bench_run(&a);
            Ok(())
        }
    }
}
