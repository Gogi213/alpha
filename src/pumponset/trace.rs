//! `alpha pumponset trace` — построчная трассировка контракта для графиков: цена, открытый интерес, z признаков и
//! срабатывания замороженных правил на 5-минутной сетке в заданных окнах (TK-492). Те же `feat::build_cube` /
//! `feat::fires` / `feat::first_triggers`, что и в `run`, — маркеры на графиках совпадают с таблицами.

use super::cache::SLOT_MIN;
use super::cli::load_contract;
use super::feat::{self, FAMILY_NAMES};
use super::params::Params;
use super::types::{Rule, F_OI_PCT, F_TRADES, F_TURN};
use crate::statarb::panel::GRID0_MS;
use anyhow::{bail, ensure, Context, Result};
use clap::Args;
use serde::Deserialize;
use std::fmt::Write as _;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Args)]
pub struct TraceArgs {
    /// Каталог кэша `*.pmc`.
    #[arg(long)]
    pub cache: PathBuf,
    #[arg(long)]
    pub params: PathBuf,
    /// `k.json` (замороженные правила): маркеры срабатываний по каждому.
    #[arg(long)]
    pub rules: PathBuf,
    /// Сдвиг OI в слотах (для Binance — 1, шаг 0).
    #[arg(long)]
    pub oi_lag: u32,
    /// csv окон `sym,t_from,t_to` (минуты от GRID0_MS, включительно).
    #[arg(long)]
    pub events: PathBuf,
    #[arg(long)]
    pub out: PathBuf,
}

#[derive(Deserialize)]
struct RuleJson {
    fam: u8,
    wi: u8,
    ti: u8,
}

pub fn trace(a: TraceArgs) -> Result<()> {
    let p = Params::load(&a.params)?;
    let text = fs::read_to_string(&a.rules).with_context(|| a.rules.display().to_string())?;
    let rj: Vec<RuleJson> = serde_json::from_str(&text).context("k.json")?;
    ensure!(!rj.is_empty(), "k.json пуст");
    let rules: Vec<Rule> = rj
        .iter()
        .map(|r| Rule {
            fam: r.fam,
            wi: r.wi,
            ti: r.ti,
        })
        .collect();
    let w60 = p
        .features
        .windows_min
        .iter()
        .position(|&w| w == 60)
        .context("в params нет окна 60 мин")?;
    let ev = fs::read_to_string(&a.events).with_context(|| a.events.display().to_string())?;
    let mut out = String::new();
    out.push_str("sym,t,t_ms,close,oi,qv5,z_f1,z_f3,z_f4");
    for r in &rules {
        let nm = rule_name(r, &p);
        let _ = write!(out, ",fire_{nm},first_{nm}");
    }
    out.push('\n');
    let mut cur: Option<(
        String,
        super::cache::Contract,
        super::types::Cube,
        Vec<Vec<usize>>,
    )> = None;
    for (ln, line) in ev.lines().enumerate().skip(1) {
        if line.trim().is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split(',').collect();
        if f.len() < 3 {
            bail!("events строка {}: нужно sym,t_from,t_to", ln + 1);
        }
        let (sym, tf, tt): (&str, i64, i64) =
            (f[0].trim(), f[1].trim().parse()?, f[2].trim().parse()?);
        if cur.as_ref().map(|c| c.0.as_str()) != Some(sym) {
            let c = load_contract(&a.cache.join(format!("{sym}.pmc")))?;
            let cube = feat::build_cube(&c, &p, a.oi_lag);
            let firsts: Vec<Vec<usize>> = rules
                .iter()
                .map(|&r| feat::first_triggers(&cube, r, &p))
                .collect();
            cur = Some((sym.to_string(), c, cube, firsts));
        }
        let (_, c, cube, firsts) = cur.as_ref().unwrap();
        let cf = c.close_ff();
        let lo = (tf.div_euclid(SLOT_MIN) - cube.slot0).max(0);
        let hi = (tt.div_euclid(SLOT_MIN) - cube.slot0).min(cube.n as i64 - 1);
        for i in lo..=hi {
            let i = i as usize;
            let t = SLOT_MIN * (cube.slot0 + i as i64);
            let k = (t - c.first_min) as usize;
            let close = cf.get(k).copied().unwrap_or(f64::NAN);
            let s = t / SLOT_MIN - i64::from(a.oi_lag);
            let oi = if s >= c.oi_first_slot {
                c.oi[0]
                    .get((s - c.oi_first_slot) as usize)
                    .copied()
                    .unwrap_or(f64::NAN)
            } else {
                f64::NAN
            };
            let qv5: f64 = (k.saturating_sub(4)..=k)
                .filter_map(|m| c.qvol.get(m))
                .map(|&x| f64::from(x))
                .sum();
            let _ = write!(
                out,
                "{sym},{t},{},{close},{oi},{qv5},{},{},{}",
                GRID0_MS + t * 60_000,
                cube.zat(F_OI_PCT, w60, i),
                cube.zat(F_TURN, w60, i),
                cube.zat(F_TRADES, w60, i)
            );
            for (ri, &r) in rules.iter().enumerate() {
                let fire = u8::from(feat::fires(cube, r, &p, i));
                let first = u8::from(firsts[ri].binary_search(&i).is_ok());
                let _ = write!(out, ",{fire},{first}");
            }
            out.push('\n');
        }
    }
    fs::write(&a.out, out).with_context(|| a.out.display().to_string())?;
    Ok(())
}

fn rule_name(r: &Rule, p: &Params) -> String {
    format!(
        "{}_w{}_th{}",
        FAMILY_NAMES[r.fam as usize],
        p.features.windows_min[r.wi as usize],
        p.features.theta[r.ti as usize]
    )
}
