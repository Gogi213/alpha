use alpha::commands::lob::{dispatch, LobCommand};
use clap::Parser;

/// Измерительный конвейер плотностей стакана (docs/plan/PLAN.md).
/// Все числа отчёта получаются отсюда: `cargo run -- <подкоманда>`.
#[derive(Debug, Parser)]
#[command(name = "alpha", version = option_env!("ALPHA_COMMIT").unwrap_or("unknown"))]
struct Cli {
    #[command(subcommand)]
    cmd: TopCommand,
}

#[derive(Debug, clap::Subcommand)]
enum TopCommand {
    /// Книга, запись, сверка, отбор (шаги 0.x плана).
    Lob {
        #[command(subcommand)]
        cmd: LobCommand,
    },
    /// Стат-арбитраж: конвертер панелей и замер ядра (TK-479).
    Statarb {
        #[command(subcommand)]
        cmd: alpha::statarb::cli::StatarbCommand,
    },
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.cmd {
        TopCommand::Lob { cmd } => dispatch(cmd),
        TopCommand::Statarb { cmd } => alpha::statarb::cli::dispatch(cmd),
    }
}
