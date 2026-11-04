//! `alpha pumponset verdict` — вердикты по таблицам `run` (спека: docs/findings/pumponset-run-spec-2026-11-05.md). Заготовка (TK-492).

use anyhow::{bail, Result};
use clap::Args;

#[derive(Debug, Args)]
pub struct VerdictArgs {}

pub fn verdict(_a: VerdictArgs) -> Result<()> {
    bail!("pumponset verdict: не реализовано")
}
