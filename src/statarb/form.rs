//! `alpha statarb form` (TK-486): формирование пар окна по панелям `.sap` — суммы Энгла–Грейнджера всех пар
//! по блокам (`egmat`) и отбор §6.1 (`select`) — в список заданий `y,x,from_day,to_day` для `statarb run`.
//!
//! Окно формирования — `window_days` суток перед `from_day` (сутки от 2020-01-01 UTC); пары торгуются
//! `from_day..to_day`. Символ с числом пар отсчётов меньше `min_obs` во вселенную не входит. Значений по
//! умолчанию нет: размер блока, `alpha` Холма, `min_obs` — параметры прогона.

use super::cli::sorted;
use super::egmat::{eg_blocks, PackedEg, CH};
use super::panel::{read_series, Series};
use super::select::{select_pairs, SelPair, SelectParams};
use anyhow::{bail, Context, Result};
use clap::Args;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Write};
use std::path::PathBuf;
use std::time::Instant;

const MIN_DAY: i64 = 1440;

#[derive(Debug, Args)]
pub struct FormArgs {
    /// Каталог панелей `<SYM>.sap`.
    #[arg(long)]
    panels: PathBuf,
    /// Первые сутки торговли пар; окно формирования — `window_days` суток перед ними.
    #[arg(long)]
    from_day: i64,
    /// Сутки конца торговли (не включая).
    #[arg(long)]
    to_day: i64,
    #[arg(long)]
    window_days: i64,
    /// Кусков по `CH` отсчётов в блоке сумм; половины окна режутся по границам блоков.
    #[arg(long)]
    block_chunks: usize,
    /// Минимум пар отсчётов (k−1, k) у пары на половине окна и у символа в окне.
    #[arg(long)]
    min_obs: u32,
    /// Уровень Холма по семье `s·(s−1)` проверок.
    #[arg(long)]
    alpha: f64,
    #[arg(long)]
    threads: usize,
    /// Выход: `y,x,from_day,to_day` (формат `statarb run --jobs`), по возрастанию p.
    #[arg(long)]
    jobs_out: PathBuf,
    /// Выход: `y,x,beta,t_first,t_second,p` в том же порядке.
    #[arg(long)]
    detail_out: Option<PathBuf>,
}

/// Ряд `ln(close)` символа на минутах `[w0, w0 + len)`; нет свечи — `NaN`.
pub fn window_row(s: &Series, w0: i64, len: usize) -> Vec<f64> {
    let mut r = vec![f64::NAN; len];
    let lo = s.first_min.max(w0);
    let hi = (s.last_min() + 1).min(w0 + len as i64);
    for m in lo..hi {
        r[(m - w0) as usize] = s.close[(m - s.first_min) as usize].ln();
    }
    r
}

/// Отобранная пара: имена ног `y`, `x` и результат отбора (`beta` — `y = α + β·x`).
pub type Formed = (String, String, SelPair);

pub fn form_pairs(
    names: &[String],
    rows: &[Vec<f64>],
    block_chunks: usize,
    min_obs: u32,
    alpha: f64,
    threads: usize,
) -> Result<Vec<Formed>> {
    let refs: Vec<&[f64]> = rows.iter().map(|v| v.as_slice()).collect();
    let p = PackedEg::new(&refs)?;
    let eb = eg_blocks(&p, block_chunks, threads);
    let sp = SelectParams {
        lo: 0,
        hi: eb.blocks,
        min_obs,
        alpha,
    };
    Ok(select_pairs(&eb, &sp)
        .into_iter()
        .map(|c| {
            let (x, y) = if c.swapped { (c.j, c.i) } else { (c.i, c.j) };
            (names[y].clone(), names[x].clone(), c)
        })
        .collect())
}

pub fn run(a: &FormArgs) -> Result<()> {
    if a.window_days < 2 || a.to_day <= a.from_day || a.block_chunks == 0 {
        bail!("window_days ≥ 2, to_day > from_day, block_chunks ≥ 1");
    }
    let t0 = Instant::now();
    let len = (a.window_days * MIN_DAY) as usize;
    let w0 = (a.from_day - a.window_days) * MIN_DAY;
    let (mut names, mut rows) = (Vec::new(), Vec::new());
    let mut thin = 0usize;
    for f in sorted(&a.panels, ".sap")? {
        let s = read_series(&mut BufReader::with_capacity(1 << 20, File::open(&f)?))
            .with_context(|| f.display().to_string())?;
        let row = window_row(&s, w0, len);
        let pairs = (1..len)
            .filter(|&k| row[k].is_finite() && row[k - 1].is_finite())
            .count();
        if pairs < a.min_obs as usize {
            thin += 1;
            continue;
        }
        names.push(f.file_stem().unwrap().to_string_lossy().into_owned());
        rows.push(row);
    }
    let t_load = t0.elapsed().as_secs_f64();
    let t1 = Instant::now();
    let formed = form_pairs(&names, &rows, a.block_chunks, a.min_obs, a.alpha, a.threads)?;
    let t_form = t1.elapsed().as_secs_f64();
    let mut jo = BufWriter::new(File::create(&a.jobs_out)?);
    writeln!(jo, "y,x,from_day,to_day")?;
    for (y, x, _) in &formed {
        writeln!(jo, "{y},{x},{},{}", a.from_day, a.to_day)?;
    }
    jo.flush()?;
    if let Some(d) = &a.detail_out {
        let mut w = BufWriter::new(fs::File::create(d)?);
        writeln!(w, "y,x,beta,t_first,t_second,p")?;
        for (y, x, c) in &formed {
            writeln!(w, "{y},{x},{},{},{},{}", c.beta, c.t_first, c.t_second, c.p)?;
        }
        w.flush()?;
    }
    let s = names.len();
    println!(
        "form: символов {s} (отсеяно по min_obs {thin}), пар {}, блоков по {} кусков ({} отсч.), окно {} сут., отобрано {}, загрузка {:.2} с, суммы+отбор {:.2} с, {}",
        s * s.saturating_sub(1) / 2,
        a.block_chunks,
        a.block_chunks * CH,
        a.window_days,
        formed.len(),
        t_load,
        t_form,
        a.jobs_out.display()
    );
    Ok(())
}
