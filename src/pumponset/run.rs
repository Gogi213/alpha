//! `alpha pumponset run` — проход по контрактам кэша, агрегация в таблицы (спека: docs/findings/pumponset-run-spec-2026-11-05.md). Заготовка (TK-492).

use anyhow::{bail, Result};
use clap::Args;

#[derive(Debug, Args)]
pub struct RunArgs {}

pub fn run(_a: RunArgs) -> Result<()> {
    bail!("pumponset run: не реализовано")
}
