//! `alpha statarb run` (TK-484): клетка П-32 × список заданий-пар на окнах суток → суточные агрегаты
//! чистых bps по суткам закрытия сделки (слой L1) и сделки выбранных заданий для ручной сверки.
//!
//! Ядро то же, что в bench. Задание — `y,x,from_day,to_day` (сутки от 2020-01-01 UTC, `to_day` не включая):
//! окно оценки греется на `window` минут до `from_day`, сделки с входом раньше `from_day` отбрасываются и
//! считаются. Суточные суммы сливаются в порядке номера задания, поэтому результат не зависит от потоков.
//! Значений по умолчанию нет: пороги, окна, стоп — параметры клетки.
//!
//! Одна клетка — флаги `--cell/--window/…` и ядро `run_pair` (эталон). `--cells <csv>` (TK-486, В-90 п.3) —
//! много клеток за один проход по минутам: клетки с общими window/min_obs/beta/refresh делят окно, β и z
//! (`multi::run_pair_multi`), панели читаются один раз. Результат клетки совпадает с её отдельным прогоном
//! побайтно (суточный csv и сделки, порядок — клетка, затем задание/сутки).

use super::cli::{common, load_names, Kind, Loaded};
use super::kernel::{run_pair, BetaMode, Counters, LegKind, PairInput, Params, Trade};
use super::multi::run_pair_multi;
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
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    cell: Option<String>,
    /// Список клеток одним проходом: csv `cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh`
    /// (`beta_fixed` пусто — как без `--beta-fixed`; `stop_z` `inf` выключает). Вместо флагов одной клетки.
    #[arg(long)]
    cells: Option<PathBuf>,
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
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    window: Option<usize>,
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    min_obs: Option<usize>,
    /// β фиксированный; без флага — OLS для pair/basket и 1 для cross/funding-*.
    #[arg(long, conflicts_with = "cells")]
    beta_fixed: Option<f64>,
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    entry_z: Option<f64>,
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    exit_z: Option<f64>,
    /// Стоп по z; `inf` выключает.
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    stop_z: Option<f64>,
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    max_hold: Option<usize>,
    #[arg(long, required_unless_present = "cells", conflicts_with = "cells")]
    refresh: Option<usize>,
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

/// Вход задания готов (`Ready`) либо нет панели / нет общего спана.
enum Prep<R> {
    Missing,
    NoSpan,
    Ready(R),
}

type Panels = HashMap<String, Loaded>;

/// Собрать вход пары для окна оценки `window` и вызвать `f`.
fn with_input<R>(
    window: usize,
    min_span: usize,
    ys: &Panels,
    xs: &Panels,
    j: &Job,
    f: impl FnOnce(&PairInput) -> R,
) -> Prep<R> {
    let Some(y) = ys.get(&j.y) else {
        return Prep::Missing;
    };
    let Some(xv) =
        j.x.iter()
            .map(|n| xs.get(n))
            .collect::<Option<Vec<&Loaded>>>()
    else {
        return Prep::Missing;
    };
    let mut all = vec![&y.s];
    all.extend(xv.iter().map(|x| &x.s));
    let Some((first, off, len)) = common(&all) else {
        return Prep::NoSpan;
    };
    let (from_min, to_min) = (j.from_day * MIN_DAY, j.to_day * MIN_DAY);
    let lo = first.max(from_min - window as i64);
    let hi = (first + len as i64 - 1).min(to_min - 1);
    if hi < lo || ((hi - lo + 1) as usize) < min_span {
        return Prep::NoSpan;
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
    Prep::Ready(f(&inp))
}

/// Сделки клетки → суточные суммы по суткам закрытия; сделки с входом до `from_min` отбрасываются и считаются.
fn tally(tr: Vec<Trade>, c: Counters, from_min: i64, keep: bool) -> JobOut {
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
    o
}

fn outcome(p: Prep<JobOut>) -> Outcome {
    match p {
        Prep::Missing => Outcome::Missing,
        Prep::NoSpan => Outcome::NoSpan,
        Prep::Ready(o) => Outcome::Done(o),
    }
}

/// Одна клетка, ядро-эталон `run_pair`.
fn run_job(p: &Params, min_span: usize, ys: &Panels, xs: &Panels, j: &Job, keep: bool) -> Outcome {
    let from_min = j.from_day * MIN_DAY;
    outcome(with_input(p.window, min_span, ys, xs, j, |inp| {
        let mut tr: Vec<Trade> = Vec::new();
        let c = run_pair(inp, p, &mut tr);
        tally(tr, c, from_min, keep)
    }))
}

struct Cell {
    label: String,
    p: Params,
}

/// Группы клеток с общими window / min_obs / beta / refresh — номера клеток в порядке файла.
fn group_cells(cells: &[Cell]) -> Vec<Vec<usize>> {
    type Key = (usize, usize, Option<u64>, usize);
    let key = |p: &Params| -> Key {
        let b = match p.beta {
            BetaMode::Ols => None,
            BetaMode::Fixed(b) => Some(b.to_bits()),
        };
        (p.window, p.min_obs, b, p.refresh)
    };
    let mut keys: Vec<Key> = Vec::new();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (i, c) in cells.iter().enumerate() {
        let k = key(&c.p);
        match keys.iter().position(|x| *x == k) {
            Some(g) => groups[g].push(i),
            None => {
                keys.push(k);
                groups.push(vec![i]);
            }
        }
    }
    groups
}

/// Все клетки одного задания: по проходу `run_pair_multi` на группу. Исход `i` — клетка `i` файла.
fn run_job_cells(
    cells: &[Cell],
    groups: &[Vec<usize>],
    min_span: usize,
    ys: &Panels,
    xs: &Panels,
    j: &Job,
    keep: bool,
) -> Vec<Outcome> {
    let from_min = j.from_day * MIN_DAY;
    let mut out: Vec<Option<Outcome>> = cells.iter().map(|_| None).collect();
    for g in groups {
        let ps: Vec<Params> = g.iter().map(|&i| cells[i].p).collect();
        let prep = with_input(ps[0].window, min_span, ys, xs, j, |inp| {
            let mut sinks: Vec<Vec<Trade>> = g.iter().map(|_| Vec::new()).collect();
            let cs = run_pair_multi(inp, &ps, &mut sinks).expect("группа клеток согласована");
            sinks
                .into_iter()
                .zip(cs)
                .map(|(tr, c)| tally(tr, c, from_min, keep))
                .collect::<Vec<JobOut>>()
        });
        match prep {
            Prep::Ready(v) => {
                for (&i, o) in g.iter().zip(v) {
                    out[i] = Some(Outcome::Done(o));
                }
            }
            Prep::Missing => g.iter().for_each(|&i| out[i] = Some(Outcome::Missing)),
            Prep::NoSpan => g.iter().for_each(|&i| out[i] = Some(Outcome::NoSpan)),
        }
    }
    out.into_iter()
        .map(|o| o.expect("клетка в группе"))
        .collect()
}

/// Параметры клетки; стороны ног и комиссии задаёт `kind`.
#[allow(clippy::too_many_arguments)]
fn cell_params(
    kind: Kind,
    window: usize,
    min_obs: usize,
    beta_fixed: Option<f64>,
    entry_z: f64,
    exit_z: f64,
    stop_z: f64,
    max_hold: usize,
    refresh: usize,
) -> Params {
    Params {
        window,
        min_obs,
        beta: match (beta_fixed, kind.two_panels()) {
            (Some(b), _) => BetaMode::Fixed(b),
            (None, true) => BetaMode::Fixed(1.0),
            (None, false) => BetaMode::Ols,
        },
        entry_z,
        exit_z,
        stop_z,
        max_hold,
        refresh,
        y_kind: LegKind::Perp,
        x_kind: if matches!(kind, Kind::FundingPs) {
            LegKind::Spot
        } else {
            LegKind::Perp
        },
        taker_in: true,
        taker_out: true,
    }
}

const CELLS_HEADER: &str = "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh";

fn read_cells(path: &Path, kind: Kind) -> Result<Vec<Cell>> {
    let txt = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    let mut v: Vec<Cell> = Vec::new();
    for (n, line) in txt.lines().enumerate().skip(1) {
        if line.trim().is_empty() {
            continue;
        }
        let at = || format!("{}:{}", path.display(), n + 1);
        let f: Vec<&str> = line.split(',').map(str::trim).collect();
        if f.len() != 9 {
            bail!("{}: нужно 9 полей {CELLS_HEADER}", at());
        }
        let beta_fixed = if f[3].is_empty() {
            None
        } else {
            Some(f[3].parse::<f64>().with_context(at)?)
        };
        let p = cell_params(
            kind,
            f[1].parse().with_context(at)?,
            f[2].parse().with_context(at)?,
            beta_fixed,
            f[4].parse().with_context(at)?,
            f[5].parse().with_context(at)?,
            f[6].parse().with_context(at)?,
            f[7].parse().with_context(at)?,
            f[8].parse().with_context(at)?,
        );
        if f[0].is_empty() || v.iter().any(|c| c.label == f[0]) {
            bail!("{}: метка клетки пуста или повторяется", at());
        }
        v.push(Cell {
            label: f[0].to_string(),
            p,
        });
    }
    if v.is_empty() {
        bail!("{}: нет клеток", path.display());
    }
    Ok(v)
}

fn one_cell(a: &RunArgs) -> Result<Cell> {
    let need = "для одной клетки нужны --cell --window --min-obs --entry-z --exit-z --stop-z --max-hold --refresh";
    let (Some(label), Some(window), Some(min_obs), Some(entry_z), Some(exit_z)) =
        (&a.cell, a.window, a.min_obs, a.entry_z, a.exit_z)
    else {
        bail!(need)
    };
    let (Some(stop_z), Some(max_hold), Some(refresh)) = (a.stop_z, a.max_hold, a.refresh) else {
        bail!(need)
    };
    Ok(Cell {
        label: label.clone(),
        p: cell_params(
            a.kind,
            window,
            min_obs,
            a.beta_fixed,
            entry_z,
            exit_z,
            stop_z,
            max_hold,
            refresh,
        ),
    })
}

/// Итог клетки по всем заданиям; сливается в порядке номера задания.
#[derive(Default)]
struct Agg {
    daily: Daily,
    c: Counters,
    pre: u64,
    done: u64,
    missing: u64,
    no_span: u64,
    trades: Vec<(usize, Trade)>,
}

impl Agg {
    fn add(&mut self, job: usize, o: Outcome) {
        match o {
            Outcome::Missing => self.missing += 1,
            Outcome::NoSpan => self.no_span += 1,
            Outcome::Done(o) => {
                self.done += 1;
                self.pre += o.pre_window;
                self.c.entries += o.c.entries;
                self.c.entry_deferred += o.c.entry_deferred;
                self.c.exit_deferred += o.c.exit_deferred;
                self.c.trades += o.c.trades;
                self.c.open_at_end += o.c.open_at_end;
                self.c.gap_minutes += o.c.gap_minutes;
                for (d, v) in o.daily {
                    let t = self.daily.entry(d).or_default();
                    for (acc, x) in t.iter_mut().zip(v) {
                        *acc += x;
                    }
                }
                self.trades.extend(o.trades.into_iter().map(|t| (job, t)));
            }
        }
    }
}

/// Слияние результатов заданий строго в порядке номера (порядок сумм f64 не зависит от потоков);
/// вышедшие вне очереди ждут в `pending`, память держится на окне потоков, а не на всех заданиях.
struct Merge {
    next: usize,
    pending: BTreeMap<usize, Vec<Outcome>>,
    aggs: Vec<Agg>,
}

impl Merge {
    fn put(&mut self, job: usize, outs: Vec<Outcome>) {
        self.pending.insert(job, outs);
        while let Some(outs) = self.pending.remove(&self.next) {
            for (agg, o) in self.aggs.iter_mut().zip(outs) {
                agg.add(self.next, o);
            }
            self.next += 1;
        }
    }
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
    let multi = a.cells.is_some();
    let cells = match &a.cells {
        Some(f) => read_cells(f, a.kind)?,
        None => vec![one_cell(a)?],
    };
    let groups = group_cells(&cells);
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
    let next = AtomicUsize::new(0);
    let merge = Mutex::new(Merge {
        next: 0,
        pending: BTreeMap::new(),
        aggs: cells.iter().map(|_| Agg::default()).collect(),
    });
    std::thread::scope(|s| {
        for _ in 0..a.threads.max(1) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(j) = jobs.get(i) else { break };
                let keep = a.trade_jobs.contains(&i);
                let outs = if multi {
                    run_job_cells(&cells, &groups, a.min_span, &ys, xs, j, keep)
                } else {
                    vec![run_job(&cells[0].p, a.min_span, &ys, xs, j, keep)]
                };
                merge.lock().unwrap().put(i, outs);
            });
        }
    });
    let run_s = t0.elapsed().as_secs_f64() - load_s;
    let aggs = merge.into_inner().unwrap().aggs;
    let mut w = BufWriter::new(File::create(&a.daily_out)?);
    writeln!(w, "cell,day,net_bps,gross_bps,fee_bps,funding_bps,trades")?;
    for (cell, agg) in cells.iter().zip(&aggs) {
        for (d, v) in &agg.daily {
            writeln!(
                w,
                "{},{},{},{},{},{},{}",
                cell.label, d, v[0], v[1], v[2], v[3], v[4] as u64
            )?;
        }
    }
    w.flush()?;
    if let Some(f) = &a.trades_out {
        let mut w = BufWriter::new(File::create(f)?);
        writeln!(
            w,
            "cell,job,y,x,dir,sig_in,fill_in,sig_out,fill_out,gross_bps,fee_bps,funding_bps,net_bps"
        )?;
        for (cell, agg) in cells.iter().zip(&aggs) {
            for (job, t) in &agg.trades {
                let j = &jobs[*job];
                writeln!(
                    w,
                    "{},{},{},{},{},{},{},{},{},{},{},{},{}",
                    cell.label,
                    job,
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
        w.flush()?;
    }
    // Одна клетка — строка как раньше; много клеток — одна строка: сделки и входы суммой по клеткам,
    // задания по первой клетке (у всех клеток одни и те же задания), по клетке — суточный csv.
    let (mut c, mut pre, mut days) = (Counters::default(), 0u64, 0usize);
    for agg in &aggs {
        c.trades += agg.c.trades;
        c.entries += agg.c.entries;
        c.entry_deferred += agg.c.entry_deferred;
        c.exit_deferred += agg.c.exit_deferred;
        c.gap_minutes += agg.c.gap_minutes;
        c.open_at_end += agg.c.open_at_end;
        pre += agg.pre;
        days += agg.daily.len();
    }
    let g0 = &aggs[0];
    let label = if multi {
        format!("[{} клеток, {} групп]", cells.len(), groups.len())
    } else {
        cells[0].label.clone()
    };
    println!(
        "run cell={} kind={:?} threads={}: заданий {} (посчитано {}, нет панели {}, нет спана {}), символов без панели Y {} / X {}, сделок {} (до окна отброшено {}), входов {}, отложено входов {} / выходов {} мин, пропусков {} мин, открыто на конце {}, суток {}, загрузка {:.1} с, счёт {:.2} с",
        label, a.kind, a.threads, jobs.len(), g0.done, g0.missing, g0.no_span, missing_y.len(), missing_x.len(),
        c.trades, pre, c.entries, c.entry_deferred, c.exit_deferred, c.gap_minutes, c.open_at_end, days, load_s, run_s
    );
    Ok(())
}
