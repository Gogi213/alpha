//! `alpha statarb run` (TK-484): клетка П-32 × список заданий-пар на окнах суток → суточные агрегаты
//! чистых bps по суткам закрытия сделки (слой L1) и сделки выбранных заданий для ручной сверки.
//!
//! Ядро то же, что в bench. Задание — `y,x,from_day,to_day` (сутки от 2020-01-01 UTC, `to_day` не включая):
//! окно оценки греется на `window` минут до `from_day`, сделки с входом раньше `from_day` отбрасываются и
//! считаются. Суточные суммы сливаются в порядке номера задания, поэтому результат не зависит от потоков.
//! Значений по умолчанию нет: пороги, окна, стоп — параметры клетки.

use super::cli::{common, load_names, Kind, Loaded};
use super::kernel::{run_pair, BetaMode, Counters, LegKind, PairInput, Params, Trade};
use anyhow::{bail, Context, Result};
use clap::Args;
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

const MIN_DAY: i64 = 1440;

#[derive(Debug, Args)]
pub struct RunArgs {
    #[arg(long, value_enum)]
    kind: Kind,
    /// Метка клетки — первый столбец суточного csv.
    #[arg(long)]
    cell: String,
    /// csv `y,x,from_day,to_day`; `x` — символы через `+` (корзина — равные веса).
    #[arg(long)]
    jobs: PathBuf,
    #[arg(long)]
    panels: PathBuf,
    /// Панели ноги X для cross / funding-*.
    #[arg(long)]
    panels_b: Option<PathBuf>,
    #[arg(long)]
    funding_y: Option<PathBuf>,
    #[arg(long)]
    funding_x: Option<PathBuf>,
    /// Выход: `cell,day,net_bps,gross_bps,fee_bps,funding_bps,trades` по суткам закрытия.
    #[arg(long)]
    daily_out: PathBuf,
    #[arg(long)]
    trades_out: Option<PathBuf>,
    /// Номера заданий (с 0, без заголовка) через запятую, чьи сделки писать в `--trades-out`.
    #[arg(long, value_delimiter = ',')]
    trade_jobs: Vec<usize>,
    /// Минимум минут общего спана вместе с прогревом.
    #[arg(long)]
    min_span: usize,
    #[arg(long)]
    threads: usize,
    #[arg(long)]
    window: usize,
    #[arg(long)]
    min_obs: usize,
    /// β фиксированный; без флага — OLS для pair/basket и 1 для cross/funding-*.
    #[arg(long)]
    beta_fixed: Option<f64>,
    #[arg(long)]
    entry_z: f64,
    #[arg(long)]
    exit_z: f64,
    /// Стоп по z; `inf` выключает.
    #[arg(long)]
    stop_z: f64,
    #[arg(long)]
    max_hold: usize,
    #[arg(long)]
    refresh: usize,
}

struct Job {
    y: String,
    x: Vec<String>,
    from_day: i64,
    to_day: i64,
}

fn read_jobs(path: &Path) -> Result<Vec<Job>> {
    let txt = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    let mut v = Vec::new();
    for (n, line) in txt.lines().enumerate().skip(1) {
        if line.trim().is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split(',').collect();
        if f.len() != 4 {
            bail!(
                "{}:{}: нужно 4 поля y,x,from_day,to_day",
                path.display(),
                n + 1
            );
        }
        let (from_day, to_day) = (f[2].trim().parse::<i64>()?, f[3].trim().parse::<i64>()?);
        if to_day <= from_day || f[1].trim().is_empty() {
            bail!("{}:{}: пустое окно или нет X", path.display(), n + 1);
        }
        v.push(Job {
            y: f[0].trim().to_string(),
            x: f[1].split('+').map(|s| s.trim().to_string()).collect(),
            from_day,
            to_day,
        });
    }
    Ok(v)
}

/// Сутки закрытия → net, gross, fee, funding, число сделок.
type Daily = BTreeMap<i64, [f64; 5]>;

#[derive(Default)]
struct JobOut {
    daily: Daily,
    c: Counters,
    pre_window: u64,
    trades: Vec<Trade>,
}

enum Outcome {
    Missing,
    NoSpan,
    Done(JobOut),
}

type Panels = HashMap<String, Loaded>;

fn run_job(p: &Params, min_span: usize, ys: &Panels, xs: &Panels, j: &Job, keep: bool) -> Outcome {
    let Some(y) = ys.get(&j.y) else {
        return Outcome::Missing;
    };
    let Some(xv) =
        j.x.iter()
            .map(|n| xs.get(n))
            .collect::<Option<Vec<&Loaded>>>()
    else {
        return Outcome::Missing;
    };
    let mut all = vec![&y.s];
    all.extend(xv.iter().map(|x| &x.s));
    let Some((first, off, len)) = common(&all) else {
        return Outcome::NoSpan;
    };
    let (from_min, to_min) = (j.from_day * MIN_DAY, j.to_day * MIN_DAY);
    let lo = first.max(from_min - p.window as i64);
    let hi = (first + len as i64 - 1).min(to_min - 1);
    if hi < lo || ((hi - lo + 1) as usize) < min_span {
        return Outcome::NoSpan;
    }
    let (len, shift) = ((hi - lo + 1) as usize, (lo - first) as usize);
    let off: Vec<usize> = off.iter().map(|o| o + shift).collect();
    let k = xv.len();
    let w = 1.0 / k as f64;
    let lx_owned: Vec<f64>;
    let lx: &[f64] = if k == 1 {
        &xv[0].ln[off[1]..off[1] + len]
    } else {
        lx_owned = (0..len)
            .map(|t| (0..k).map(|i| w * xv[i].ln[off[i + 1] + t]).sum())
            .collect();
        &lx_owned
    };
    let ox: Vec<&[f64]> = (0..k)
        .map(|i| &xv[i].s.open[off[i + 1]..off[i + 1] + len])
        .collect();
    let wx = vec![w; k];
    let fx: Vec<&[(i64, f64)]> = xv.iter().map(|x| x.fund.as_slice()).collect();
    let inp = PairInput {
        first_min: lo,
        ly: &y.ln[off[0]..off[0] + len],
        lx,
        oy: &y.s.open[off[0]..off[0] + len],
        ox: &ox,
        wx: &wx,
        fund_y: &y.fund,
        fund_x: &fx,
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, p, &mut tr);
    let mut o = JobOut {
        c,
        ..JobOut::default()
    };
    for t in tr {
        if t.fill_in < from_min {
            o.pre_window += 1;
            continue;
        }
        let d = o.daily.entry(t.fill_out.div_euclid(MIN_DAY)).or_default();
        d[0] += t.net_bps;
        d[1] += t.gross_bps;
        d[2] += t.fee_bps;
        d[3] += t.funding_bps;
        d[4] += 1.0;
        if keep {
            o.trades.push(t);
        }
    }
    Outcome::Done(o)
}

fn uniq(jobs: &[Job], f: impl Fn(&Job) -> Vec<&String>) -> Vec<String> {
    let mut v: Vec<String> = jobs.iter().flat_map(f).cloned().collect();
    v.sort();
    v.dedup();
    v
}

pub fn run(a: &RunArgs) -> Result<()> {
    let t0 = Instant::now();
    let two = a.kind.two_panels();
    if two && a.panels_b.is_none() {
        bail!("cross / funding-* требует --panels-b");
    }
    if matches!(a.kind, Kind::FundingPp | Kind::FundingPs) && a.funding_y.is_none() {
        bail!("funding-* требует --funding-y");
    }
    if matches!(a.kind, Kind::FundingPp) && a.funding_x.is_none() {
        bail!("funding-pp требует --funding-x");
    }
    if !a.trade_jobs.is_empty() && a.trades_out.is_none() {
        bail!("--trade-jobs требует --trades-out");
    }
    let jobs = read_jobs(&a.jobs)?;
    let (ys, missing_y, xs_b, missing_x);
    if let Some(pb) = a.panels_b.as_deref().filter(|_| two) {
        (ys, missing_y) = load_names(
            &a.panels,
            &uniq(&jobs, |j| vec![&j.y]),
            a.funding_y.as_deref(),
        )?;
        (xs_b, missing_x) = load_names(
            pb,
            &uniq(&jobs, |j| j.x.iter().collect()),
            a.funding_x.as_deref(),
        )?;
    } else {
        // одна биржа: общая карта символов, ноги Y и X читают её
        let all = uniq(&jobs, |j| std::iter::once(&j.y).chain(j.x.iter()).collect());
        (ys, missing_y) = load_names(&a.panels, &all, a.funding_y.as_deref())?;
        (xs_b, missing_x) = (Panels::new(), Vec::new());
    }
    let xs: &Panels = if two { &xs_b } else { &ys };
    let load_s = t0.elapsed().as_secs_f64();
    let p = Params {
        window: a.window,
        min_obs: a.min_obs,
        beta: match (a.beta_fixed, two) {
            (Some(b), _) => BetaMode::Fixed(b),
            (None, true) => BetaMode::Fixed(1.0),
            (None, false) => BetaMode::Ols,
        },
        entry_z: a.entry_z,
        exit_z: a.exit_z,
        stop_z: a.stop_z,
        max_hold: a.max_hold,
        refresh: a.refresh,
        y_kind: LegKind::Perp,
        x_kind: if matches!(a.kind, Kind::FundingPs) {
            LegKind::Spot
        } else {
            LegKind::Perp
        },
        taker_in: true,
        taker_out: true,
    };
    let next = AtomicUsize::new(0);
    let slots: Mutex<Vec<Option<Outcome>>> = Mutex::new((0..jobs.len()).map(|_| None).collect());
    std::thread::scope(|s| {
        for _ in 0..a.threads.max(1) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(j) = jobs.get(i) else { break };
                let r = run_job(&p, a.min_span, &ys, xs, j, a.trade_jobs.contains(&i));
                slots.lock().unwrap()[i] = Some(r);
            });
        }
    });
    let run_s = t0.elapsed().as_secs_f64() - load_s;
    let (mut daily, mut c, mut pre) = (Daily::new(), Counters::default(), 0u64);
    let (mut done, mut missing, mut no_span) = (0u64, 0u64, 0u64);
    let mut tw = match &a.trades_out {
        Some(f) => {
            let mut w = BufWriter::new(File::create(f)?);
            writeln!(
                w,
                "cell,job,y,x,dir,sig_in,fill_in,sig_out,fill_out,gross_bps,fee_bps,funding_bps,net_bps"
            )?;
            Some(w)
        }
        None => None,
    };
    for (i, slot) in slots.into_inner().unwrap().into_iter().enumerate() {
        match slot.expect("задание не выполнено") {
            Outcome::Missing => missing += 1,
            Outcome::NoSpan => no_span += 1,
            Outcome::Done(o) => {
                done += 1;
                pre += o.pre_window;
                c.entries += o.c.entries;
                c.entry_deferred += o.c.entry_deferred;
                c.exit_deferred += o.c.exit_deferred;
                c.trades += o.c.trades;
                c.open_at_end += o.c.open_at_end;
                c.gap_minutes += o.c.gap_minutes;
                for (d, v) in o.daily {
                    let t = daily.entry(d).or_default();
                    for (acc, x) in t.iter_mut().zip(v) {
                        *acc += x;
                    }
                }
                if let Some(w) = tw.as_mut() {
                    let j = &jobs[i];
                    for t in &o.trades {
                        writeln!(
                            w,
                            "{},{},{},{},{},{},{},{},{},{},{},{},{}",
                            a.cell,
                            i,
                            j.y,
                            j.x.join("+"),
                            t.dir,
                            t.sig_in,
                            t.fill_in,
                            t.sig_out,
                            t.fill_out,
                            t.gross_bps,
                            t.fee_bps,
                            t.funding_bps,
                            t.net_bps
                        )?;
                    }
                }
            }
        }
    }
    let mut w = BufWriter::new(File::create(&a.daily_out)?);
    writeln!(w, "cell,day,net_bps,gross_bps,fee_bps,funding_bps,trades")?;
    for (d, v) in &daily {
        writeln!(
            w,
            "{},{},{},{},{},{},{}",
            a.cell, d, v[0], v[1], v[2], v[3], v[4] as u64
        )?;
    }
    w.flush()?;
    if let Some(mut w) = tw {
        w.flush()?;
    }
    println!(
        "run cell={} kind={:?} threads={}: заданий {} (посчитано {}, нет панели {}, нет спана {}), символов без панели Y {} / X {}, сделок {} (до окна отброшено {}), входов {}, отложено входов {} / выходов {} мин, пропусков {} мин, открыто на конце {}, суток {}, загрузка {:.1} с, счёт {:.2} с",
        a.cell, a.kind, a.threads, jobs.len(), done, missing, no_span, missing_y.len(), missing_x.len(),
        c.trades, pre, c.entries, c.entry_deferred, c.exit_deferred, c.gap_minutes, c.open_at_end, daily.len(), load_s, run_s
    );
    Ok(())
}
