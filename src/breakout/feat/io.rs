//! Вход/выход `alpha-feat`: CSV эпизодов → потоки по монетам → CSV признаков (порядок строк — как на входе).
//!
//! Вход: `ep_id,sym,side,sig_ms,turn24h` (`side` `long` → +1, иначе −1; `turn24h` пусто → NaN; `sig_ms` — мс от `--base-date`).
//! Выход: `ep_id,<16 FLOW>,<14 DERIV>`; NaN — пусто, float — кратчайшая круговая запись (`{:?}`: всегда с точкой или порядком,
//! `-0.0` сохраняется); в режиме `flow` столбцы DERIV пустые и наоборот (как у `tk594_feat.py`).

use super::deriv::{deriv_coin, CoinDeriv, DerivDirs, DERIV};
use super::flow::{flow_coin, CoinFlow, Ep, FLOW};
use super::trades::{DaySrc, ZipDaySrc};
use anyhow::{bail, Context, Result};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::time::Instant;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Flow,
    Deriv,
    All,
}

pub struct Cfg {
    pub mode: Mode,
    pub eps: PathBuf,
    pub out: PathBuf,
    /// `--base-date` в мс от 1970 (`sig_ms` входа считается от неё).
    pub base_ms: i64,
    pub threads: usize,
    pub coins: Option<Vec<String>>,
    pub trades: Vec<PathBuf>,
    pub deriv: DerivDirs,
}

/// Итог прогона (последняя строка stdout — JSON).
#[derive(Debug, Clone)]
pub struct Summary {
    pub episodes: usize,
    pub coins: usize,
    pub seconds: f64,
    pub missing_trade_days: u64,
    pub unsorted_days: u64,
}

impl Summary {
    pub fn json(&self) -> String {
        format!(
            "{{\"episodes\":{},\"coins\":{},\"seconds\":{:.1},\"missing_trade_days\":{},\"unsorted_days\":{}}}",
            self.episodes, self.coins, self.seconds, self.missing_trade_days, self.unsorted_days
        )
    }
}

pub struct Input {
    pub ep_id: String,
    pub sym: String,
    pub ep: Ep,
}

/// Читает CSV эпизодов; `Ep::row` — номер строки среди оставшихся (после фильтра `coins`).
pub fn read_eps(path: &Path, base_ms: i64, coins: Option<&[String]>) -> Result<Vec<Input>> {
    let mut rd = csv::ReaderBuilder::new()
        .has_headers(true)
        .from_path(path)
        .with_context(|| path.display().to_string())?;
    let head = rd.headers()?.clone();
    let col = |n: &str| {
        head.iter()
            .position(|h| h == n)
            .with_context(|| format!("во входе нет столбца {n}"))
    };
    let (i_id, i_sym, i_side, i_sig, i_turn) = (
        col("ep_id")?,
        col("sym")?,
        col("side")?,
        col("sig_ms")?,
        col("turn24h")?,
    );
    let mut v: Vec<Input> = Vec::new();
    for (ln, rec) in rd.records().enumerate() {
        let rec = rec?;
        let sym = rec.get(i_sym).unwrap_or("");
        if coins.is_some_and(|c| !c.iter().any(|x| x == sym)) {
            continue;
        }
        let ctx = || format!("строка {}", ln + 2);
        let sig_ms: i64 = rec
            .get(i_sig)
            .unwrap_or("")
            .trim()
            .parse()
            .with_context(ctx)?;
        let turn = rec.get(i_turn).unwrap_or("").trim();
        let turn24h = if turn.is_empty() {
            f64::NAN
        } else {
            turn.parse::<f64>().with_context(ctx)?
        };
        let side = if rec.get(i_side).unwrap_or("") == "long" {
            1
        } else {
            -1
        };
        let row = v.len();
        v.push(Input {
            ep_id: rec.get(i_id).unwrap_or("").to_string(),
            sym: sym.to_string(),
            ep: Ep {
                row,
                sig: base_ms + sig_ms,
                side,
                turn24h,
            },
        });
    }
    Ok(v)
}

#[derive(Default)]
struct Done {
    flow: Option<CoinFlow>,
    deriv: Option<CoinDeriv>,
}

fn run_coin(cfg: &Cfg, src: &dyn DaySrc, sym: &str, eps: &[Ep]) -> Result<Done> {
    let mut d = Done::default();
    if matches!(cfg.mode, Mode::Deriv | Mode::All) {
        d.deriv = Some(deriv_coin(&cfg.deriv, sym, eps)?);
    }
    if matches!(cfg.mode, Mode::Flow | Mode::All) {
        d.flow = Some(flow_coin(src, sym, eps)?);
    }
    Ok(d)
}

fn push_f64(out: &mut Vec<String>, x: f64) {
    let mut s = String::new();
    if !x.is_nan() {
        let _ = write!(s, "{x:?}");
    }
    out.push(s);
}

pub fn run(cfg: &Cfg) -> Result<Summary> {
    let t0 = Instant::now();
    let inputs = read_eps(&cfg.eps, cfg.base_ms, cfg.coins.as_deref())?;
    if inputs.is_empty() {
        bail!("пустой вход: {}", cfg.eps.display());
    }
    let mut by_sym: BTreeMap<&str, Vec<Ep>> = BTreeMap::new();
    for i in &inputs {
        by_sym.entry(i.sym.as_str()).or_default().push(i.ep);
    }
    let mut jobs: Vec<(&str, Vec<Ep>)> = by_sym.into_iter().collect();
    jobs.sort_by_key(|(_, e)| std::cmp::Reverse(e.len()));
    eprintln!("эпизодов {}; монет {}", inputs.len(), jobs.len());
    let src = ZipDaySrc {
        roots: cfg.trades.clone(),
    };
    let next = AtomicUsize::new(0);
    let finished = AtomicUsize::new(0);
    let results: Mutex<Vec<(usize, Result<Done>)>> = Mutex::new(Vec::with_capacity(jobs.len()));
    std::thread::scope(|s| {
        for _ in 0..cfg.threads.max(1) {
            s.spawn(|| loop {
                let j = next.fetch_add(1, Ordering::SeqCst);
                let Some((sym, eps)) = jobs.get(j) else { break };
                let r = run_coin(cfg, &src, sym, eps).with_context(|| format!("монета {sym}"));
                results
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .push((j, r));
                let n = finished.fetch_add(1, Ordering::SeqCst) + 1;
                if n.is_multiple_of(20) {
                    eprintln!(
                        "{n}/{} монет, {:.0} с",
                        jobs.len(),
                        t0.elapsed().as_secs_f64()
                    );
                }
            });
        }
    });
    let mut table: Vec<[f64; 30]> = vec![[f64::NAN; 30]; inputs.len()];
    let (mut missing, mut unsorted) = (0u64, 0u64);
    for (_, r) in results.into_inner().unwrap_or_else(PoisonError::into_inner) {
        let d = r?;
        if let Some(f) = d.flow {
            missing += f.missing_trade_days;
            unsorted += f.unsorted_days;
            for (row, vals) in f.rows {
                table[row][..16].copy_from_slice(&vals);
            }
        }
        if let Some(dv) = d.deriv {
            for (row, vals) in dv.rows {
                table[row][16..].copy_from_slice(&vals);
            }
        }
    }
    let mut w = csv::WriterBuilder::new()
        .from_path(&cfg.out)
        .with_context(|| cfg.out.display().to_string())?;
    let mut head = vec!["ep_id".to_string()];
    head.extend(FLOW.iter().chain(DERIV.iter()).map(|s| (*s).to_string()));
    w.write_record(&head)?;
    for (i, inp) in inputs.iter().enumerate() {
        let mut rec = vec![inp.ep_id.clone()];
        for &x in &table[i] {
            push_f64(&mut rec, x);
        }
        w.write_record(&rec)?;
    }
    w.flush()?;
    Ok(Summary {
        episodes: inputs.len(),
        coins: jobs.len(),
        seconds: t0.elapsed().as_secs_f64(),
        missing_trade_days: missing,
        unsorted_days: unsorted,
    })
}
