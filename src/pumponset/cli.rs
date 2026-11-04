//! `alpha pumponset cache|run|bench` (TK-492): csv.gz → кэш `.pmc` один раз; прогон ядра по контрактам.
//! Окна, пороги, зерно — не здесь: параметры из `params.json`, зерно и сдвиг OI — аргументы запуска.

use super::cache::{is_stale, parse_candles, parse_oi, read_contract, write_contract, Contract};
use crate::statarb::cli::parse_funding;
use anyhow::{bail, Context, Result};
use clap::Subcommand;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

#[derive(Debug, Subcommand)]
pub enum PumponsetCommand {
    /// Каталоги `<SYM>.csv.gz` свечей/OI/фандинга → каталог `<SYM>.pmc`; пересобирается только устаревшее
    /// (файл-источник новее кэша).
    Cache {
        #[arg(long)]
        candles: PathBuf,
        #[arg(long)]
        oi: PathBuf,
        #[arg(long)]
        funding: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        threads: usize,
    },
    /// Прогон ядра по кэшу: признаки, эпизоды, контроли → таблицы csv (`docs/findings/pumponset-run-spec-2026-11-05.md`).
    Run(super::run::RunArgs),
    /// Бутстрэп по суткам, Холм, отбор K, вердикты по таблицам `run`.
    Verdict(super::verdict::VerdictArgs),
}

pub fn dispatch(cmd: PumponsetCommand) -> Result<()> {
    match cmd {
        PumponsetCommand::Cache {
            candles,
            oi,
            funding,
            out,
            threads,
        } => cache(&candles, &oi, &funding, &out, threads),
        PumponsetCommand::Run(a) => super::run::run(a),
        PumponsetCommand::Verdict(a) => super::verdict::verdict(a),
    }
}

pub(crate) fn sorted(dir: &Path, ext: &str) -> Result<Vec<PathBuf>> {
    let mut v: Vec<PathBuf> = fs::read_dir(dir)
        .with_context(|| format!("каталог {}", dir.display()))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.to_string_lossy().ends_with(ext))
        .collect();
    v.sort();
    Ok(v)
}

pub(crate) fn sym_of(p: &Path, ext: &str) -> String {
    p.file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .replace(ext, "")
}

/// Читает контракт из кэша `<dir>/<sym>.pmc`.
pub fn load_contract(path: &Path) -> Result<Contract> {
    let f = File::open(path).with_context(|| path.display().to_string())?;
    read_contract(&mut BufReader::with_capacity(1 << 20, f))
        .with_context(|| path.display().to_string())
}

/// `gzip -dc` файла: поток строк csv без распаковки в память целиком.
fn with_gz<T>(
    path: &Path,
    f: impl FnOnce(BufReader<std::process::ChildStdout>) -> Result<T>,
) -> Result<T> {
    let mut child = Command::new("gzip")
        .arg("-dc")
        .arg(path)
        .stdout(Stdio::piped())
        .spawn()
        .context("gzip -dc")?;
    let rd = BufReader::with_capacity(1 << 20, child.stdout.take().context("stdout")?);
    let r = f(rd).with_context(|| path.display().to_string());
    if r.is_err() {
        let _ = child.kill();
    }
    if !child.wait()?.success() && r.is_ok() {
        bail!("gzip -dc {}: ненулевой код", path.display());
    }
    r
}

/// Итог одного контракта: `None` — свечей нет (пустой файл), контракт пропущен.
struct Built {
    candle_rows: u64,
    oi_rows: u64,
    funding_rows: usize,
}

fn cache_one(candles: &Path, oi: &Path, funding: &Path, out: &Path) -> Result<Option<Built>> {
    let (c, st) = with_gz(candles, |rd| Ok(parse_candles(rd)?))?;
    let Some(mut c) = c else {
        return Ok(None);
    };
    let oi_rows = if oi.exists() {
        with_gz(oi, |rd| Ok(parse_oi(rd, &mut c)?))?
    } else {
        0
    };
    if funding.exists() {
        let text = with_gz(funding, |mut rd| {
            let mut s = String::new();
            std::io::Read::read_to_string(&mut rd, &mut s)?;
            Ok(s)
        })?;
        c.funding = parse_funding(&text)?;
    }
    let tmp = out.with_extension("pmc.tmp");
    let mut w = BufWriter::with_capacity(1 << 20, File::create(&tmp)?);
    write_contract(&mut w, &c)?;
    std::io::Write::flush(&mut w)?;
    drop(w);
    fs::rename(&tmp, out)?;
    Ok(Some(Built {
        candle_rows: st.rows,
        oi_rows,
        funding_rows: c.funding.len(),
    }))
}

fn cache(candles: &Path, oi: &Path, funding: &Path, out: &Path, threads: usize) -> Result<()> {
    fs::create_dir_all(out)?;
    let files = sorted(candles, ".csv.gz")?;
    let next = AtomicUsize::new(0);
    // (собрано, актуально, пустых, свечей, OI-строк, ставок, ошибки)
    let acc = Mutex::new((
        0usize,
        0usize,
        0usize,
        0u64,
        0u64,
        0usize,
        Vec::<String>::new(),
    ));
    let t0 = Instant::now();
    std::thread::scope(|s| {
        for _ in 0..threads.max(1) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(f) = files.get(i) else { break };
                let sym = sym_of(f, ".csv.gz");
                let (o, fu) = (
                    oi.join(format!("{sym}.csv.gz")),
                    funding.join(format!("{sym}.csv.gz")),
                );
                let dst = out.join(format!("{sym}.pmc"));
                let mut srcs: Vec<&Path> = vec![f.as_path()];
                srcs.extend(
                    [o.as_path(), fu.as_path()]
                        .into_iter()
                        .filter(|p| p.exists()),
                );
                let r = if is_stale(&dst, &srcs) {
                    cache_one(f, &o, &fu, &dst).map(|b| (true, b))
                } else {
                    Ok((false, None))
                };
                let mut a = acc.lock().unwrap();
                match r {
                    Ok((true, Some(b))) => {
                        a.0 += 1;
                        a.3 += b.candle_rows;
                        a.4 += b.oi_rows;
                        a.5 += b.funding_rows;
                    }
                    Ok((true, None)) => a.2 += 1,
                    Ok((false, _)) => a.1 += 1,
                    Err(e) => a.6.push(format!("{sym}: {e:#}")),
                }
            });
        }
    });
    let a = acc.into_inner().unwrap();
    println!(
        "cache: файлов {} (собрано {}, актуально {}, пустых {}, ошибок {}), строк свечей {}, OI {}, ставок {}, {:.1} с",
        files.len(), a.0, a.1, a.2, a.6.len(), a.3, a.4, a.5, t0.elapsed().as_secs_f64()
    );
    for e in a.6.iter().take(10) {
        println!("ошибка {e}");
    }
    if a.6.is_empty() {
        Ok(())
    } else {
        bail!("{} файлов с ошибкой", a.6.len())
    }
}
