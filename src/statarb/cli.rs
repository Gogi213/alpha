//! `alpha statarb convert|bench` (TK-479): csv.gz свечей → панели `.sap` один раз; замер
//! пропускной способности ядра по видам. Пороги и окна не назначаются — все параметры обязательны.

use super::kernel::{run_pair, BetaMode, Counters, LegKind, PairInput, Params, Rule, Sink, Trade};
use super::panel::{
    convert_csv_stats, read_series, write_series, ConvertStats, Series, GRID0_MS, MINUTE_MS,
};
use super::reference;
use anyhow::{bail, Context, Result};
use clap::{Subcommand, ValueEnum};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{BufReader, BufWriter};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

#[derive(Debug, Subcommand)]
pub enum StatarbCommand {
    /// Каталог `<SYM>.csv.gz` → каталог `<SYM>.sap` (минуты с объёмом 0 и дыры → NaN).
    Convert {
        #[arg(long)]
        src: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        threads: usize,
    },
    /// Замер скорости ядра на панелях: строк (минут) в секунду и пар в секунду.
    Bench {
        /// pair — все пары первых N символов; cross — один символ на двух биржах (β = 1);
        /// basket — символ против K следующих с равными весами; funding-pp — перп против перпа
        /// другой биржи с выплатами фандинга обеих ног; funding-ps — перп против спота
        /// (спот-ног фандинга нет, комиссия спота).
        #[arg(long, value_enum)]
        kind: Kind,
        /// kernel — онлайн-ядро; reference — наивный O(W) пересчёт; drift — ядро и reference на тех же
        /// заданиях, печатает расхождение z и сделок (скорость не мерится).
        #[arg(long, value_enum)]
        engine: Engine,
        #[arg(long)]
        panels: PathBuf,
        /// Вторая биржа для cross / funding-*.
        #[arg(long)]
        panels_b: Option<PathBuf>,
        /// Каталог `<SYM>.csv.gz` фандинга ноги Y (funding-*).
        #[arg(long)]
        funding_y: Option<PathBuf>,
        /// Каталог `<SYM>.csv.gz` фандинга ноги X (funding-pp).
        #[arg(long)]
        funding_x: Option<PathBuf>,
        /// Сколько первых (по имени) панелей брать.
        #[arg(long)]
        symbols: usize,
        /// Потолок числа заданий (пар/корзин).
        #[arg(long)]
        max_jobs: usize,
        /// Минимум общих минут спана у задания.
        #[arg(long)]
        min_span: usize,
        /// K компонент корзины (только basket).
        #[arg(long, default_value_t = 0)]
        basket_k: usize,
        #[arg(long)]
        threads: usize,
        #[arg(long)]
        window: usize,
        #[arg(long)]
        min_obs: usize,
        #[arg(long)]
        entry_z: f64,
        #[arg(long)]
        exit_z: f64,
        #[arg(long)]
        max_hold: usize,
        #[arg(long)]
        refresh: usize,
        /// Печатает хеш всех сделок и счётчиков (kernel / reference): гейт «байт в байт» между бинарниками.
        /// Без флага горячий путь идёт с пустым приёмником и вывод прежний.
        #[arg(long, default_value_t = false)]
        digest: bool,
        /// C1: задержка входа ноги X после ноги Y, минут; 0 — обе ноги на одном open (как раньше).
        #[arg(long, default_value_t = 0)]
        delay_x: usize,
        /// D3: k последних выплат ноги Y для входа по уровню ставки (требует --funding-y); 0 — правило z (как раньше).
        #[arg(long, default_value_t = 0)]
        funding_k: usize,
        /// D3: ожидаемое удержание в выплатах (обязательно при --funding-k ≥ 1).
        #[arg(long, default_value_t = 0)]
        hold_payments: usize,
    },
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum Kind {
    Pair,
    Cross,
    Basket,
    FundingPp,
    FundingPs,
}

impl Kind {
    fn two_panels(self) -> bool {
        matches!(self, Kind::Cross | Kind::FundingPp | Kind::FundingPs)
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum Engine {
    Kernel,
    Reference,
    Drift,
}

/// Расхождение ядра с референсом на одном задании.
#[derive(Debug, Clone, Copy, Default)]
struct Drift {
    z_cmp: u64,
    z_nan_diff: u64,
    max_dz: f64,
    trades_k: u64,
    trades_r: u64,
    trades_diff: u64,
    max_dnet: f64,
}

impl Drift {
    fn add(&mut self, o: &Drift) {
        self.z_cmp += o.z_cmp;
        self.z_nan_diff += o.z_nan_diff;
        self.max_dz = self.max_dz.max(o.max_dz);
        self.trades_k += o.trades_k;
        self.trades_r += o.trades_r;
        self.trades_diff += o.trades_diff;
        self.max_dnet = self.max_dnet.max(o.max_dnet);
    }
}

/// Приёмник замера дрейфа: сделки и z-скор каждой минуты.
struct DriftSink {
    z: Vec<f64>,
    tr: Vec<Trade>,
}

impl Sink for DriftSink {
    fn trade(&mut self, t: &Trade) {
        self.tr.push(*t);
    }
    fn z(&mut self, t: usize, z: f64) {
        self.z[t] = z;
    }
}

/// FNV-1a по побитовому представлению сделок: два бинарника выдают один хеш, только если все сделки совпали до бита.
struct HashSink(u64);

impl HashSink {
    const SEED: u64 = 0xcbf2_9ce4_8422_2325;

    fn eat(&mut self, v: u64) {
        for b in v.to_le_bytes() {
            self.0 = (self.0 ^ u64::from(b)).wrapping_mul(0x0100_0000_01b3);
        }
    }

    fn counters(&mut self, c: &Counters) {
        for v in [
            c.obs_minutes,
            c.gap_minutes,
            c.entries,
            c.entry_deferred,
            c.exit_deferred,
            c.trades,
            c.minutes_in_pos,
            c.open_at_end,
            c.sum_net_bps.to_bits(),
        ] {
            self.eat(v);
        }
        // Поля, которых не было в прежнем бинарнике, входят в хеш только при ненулевом значении.
        if c.x_deferred != 0 {
            self.eat(c.x_deferred);
        }
    }
}

impl Sink for HashSink {
    fn trade(&mut self, t: &Trade) {
        if t.fill_x != t.fill_in {
            self.eat(t.fill_x as u64);
        }
        for v in [
            t.dir as u64,
            t.sig_in as u64,
            t.fill_in as u64,
            t.sig_out as u64,
            t.fill_out as u64,
            t.gross_bps.to_bits(),
            t.fee_bps.to_bits(),
            t.funding_bps.to_bits(),
            t.net_bps.to_bits(),
        ] {
            self.eat(v);
        }
    }
}

type TradeKey = (i8, i64, i64, i64, i64);

fn key(t: &Trade) -> TradeKey {
    (t.dir, t.sig_in, t.fill_in, t.sig_out, t.fill_out)
}

fn drift(inp: &PairInput, p: &Params) -> Drift {
    let mut k = DriftSink {
        z: vec![f64::NAN; inp.ly.len()],
        tr: Vec::new(),
    };
    run_pair(inp, p, &mut k);
    let mut r: Vec<Trade> = Vec::new();
    reference::run_pair(inp, p, &mut r);
    let zr = reference::z_series(inp, p);
    let mut d = Drift::default();
    for (a, (_, b)) in k.z.iter().zip(&zr) {
        match (a.is_finite(), b.is_finite()) {
            (true, true) => {
                d.z_cmp += 1;
                d.max_dz = d.max_dz.max((a - b).abs());
            }
            (false, false) => {}
            _ => d.z_nan_diff += 1,
        }
    }
    let by_key: HashMap<TradeKey, f64> = r.iter().map(|t| (key(t), t.net_bps)).collect();
    d.trades_k = k.tr.len() as u64;
    d.trades_r = r.len() as u64;
    let mut matched = 0u64;
    for t in &k.tr {
        if let Some(n) = by_key.get(&key(t)) {
            matched += 1;
            d.max_dnet = d.max_dnet.max((t.net_bps - n).abs());
        }
    }
    // Симметрическая разность множеств сделок: ядро без пары в референсе + референс без пары в ядре.
    d.trades_diff = (d.trades_k - matched) + (d.trades_r - matched);
    d
}

pub fn dispatch(cmd: StatarbCommand) -> Result<()> {
    match cmd {
        StatarbCommand::Convert { src, out, threads } => convert(&src, &out, threads),
        StatarbCommand::Bench {
            kind,
            engine,
            panels,
            panels_b,
            funding_y,
            funding_x,
            symbols,
            max_jobs,
            min_span,
            basket_k,
            threads,
            window,
            min_obs,
            entry_z,
            exit_z,
            max_hold,
            refresh,
            digest,
            delay_x,
            funding_k,
            hold_payments,
        } => {
            if funding_k > 0 && (hold_payments == 0 || funding_y.is_none()) {
                bail!("--funding-k требует --hold-payments ≥ 1 и --funding-y");
            }
            let p = Params {
                window,
                min_obs,
                beta: if kind.two_panels() {
                    BetaMode::Fixed(1.0)
                } else {
                    BetaMode::Ols
                },
                entry_z,
                exit_z,
                stop_z: f64::INFINITY,
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
                delay_x,
                rule: if funding_k > 0 {
                    Rule::Funding {
                        k: funding_k,
                        hold_payments,
                    }
                } else {
                    Rule::Z
                },
            };
            bench(&BenchArgs {
                kind,
                engine,
                panels: &panels,
                panels_b: panels_b.as_deref(),
                funding_y: funding_y.as_deref(),
                funding_x: funding_x.as_deref(),
                symbols,
                max_jobs,
                min_span,
                basket_k,
                threads,
                digest,
                p,
            })
        }
    }
}

fn sorted(dir: &Path, ext: &str) -> Result<Vec<PathBuf>> {
    let mut v: Vec<PathBuf> = fs::read_dir(dir)
        .with_context(|| format!("каталог {}", dir.display()))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.to_string_lossy().ends_with(ext))
        .collect();
    v.sort();
    Ok(v)
}

fn convert_one(src: &Path, out: &Path) -> Result<(ConvertStats, u64, bool)> {
    let mut child = Command::new("gzip")
        .arg("-dc")
        .arg(src)
        .stdout(Stdio::piped())
        .spawn()
        .context("gzip -dc")?;
    let rd = BufReader::with_capacity(1 << 20, child.stdout.take().context("stdout")?);
    let (ser, st) = convert_csv_stats(rd).with_context(|| src.display().to_string())?;
    if !child.wait()?.success() {
        bail!("gzip -dc {}: ненулевой код", src.display());
    }
    let Some(ser) = ser else {
        return Ok((st, 0, false));
    };
    let mut w = BufWriter::with_capacity(1 << 20, File::create(out)?);
    write_series(&mut w, &ser)?;
    Ok((st, ser.len() as u64, true))
}

fn convert(src: &Path, out: &Path, threads: usize) -> Result<()> {
    fs::create_dir_all(out)?;
    let files = sorted(src, ".csv.gz")?;
    let next = AtomicUsize::new(0);
    let acc = Mutex::new((
        ConvertStats::default(),
        0u64,
        0usize,
        0usize,
        Vec::<String>::new(),
    ));
    let t0 = Instant::now();
    std::thread::scope(|s| {
        for _ in 0..threads.max(1) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(f) = files.get(i) else { break };
                let sym = f
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .replace(".csv.gz", "");
                let r = convert_one(f, &out.join(format!("{sym}.sap")));
                let mut a = acc.lock().unwrap();
                match r {
                    Ok((st, n, wrote)) => {
                        a.0.rows += st.rows;
                        a.0.zero_volume += st.zero_volume;
                        a.1 += n;
                        a.2 += usize::from(wrote);
                        a.3 += usize::from(!wrote);
                    }
                    Err(e) => a.4.push(format!("{sym}: {e:#}")),
                }
            });
        }
    });
    let a = acc.into_inner().unwrap();
    let wall = t0.elapsed().as_secs_f64();
    println!(
        "convert: файлов {} (панелей {}, пустых {}, ошибок {}), строк csv {}, с объёмом 0 → NaN {}, минут в панелях {}, {:.1} с, {:.0} строк/с",
        files.len(), a.2, a.3, a.4.len(), a.0.rows, a.0.zero_volume, a.1, wall, a.0.rows as f64 / wall
    );
    for e in a.4.iter().take(10) {
        println!("ошибка {e}");
    }
    if a.4.is_empty() {
        Ok(())
    } else {
        bail!("{} файлов с ошибкой", a.4.len())
    }
}

struct BenchArgs<'a> {
    kind: Kind,
    engine: Engine,
    panels: &'a Path,
    panels_b: Option<&'a Path>,
    funding_y: Option<&'a Path>,
    funding_x: Option<&'a Path>,
    symbols: usize,
    max_jobs: usize,
    min_span: usize,
    basket_k: usize,
    threads: usize,
    digest: bool,
    p: Params,
}

struct Loaded {
    name: String,
    s: Series,
    ln: Vec<f64>,
    /// Выплаты фандинга `(минута, ставка)` по возрастанию; пусто — файла нет или спот.
    fund: Vec<(i64, f64)>,
}

/// Минута сетки панелей (индекс от `GRID0_MS`) для времени выплаты в мс unix: ставки и `.sap` в одной эпохе (E-010).
pub fn funding_min(time_ms: i64) -> i64 {
    (time_ms - GRID0_MS).div_euclid(MINUTE_MS)
}

/// `time_ms,rate,interval_h` → `(funding_min(time_ms), rate)`; первая строка — заголовок.
pub fn parse_funding(text: &str) -> Result<Vec<(i64, f64)>> {
    let mut v = Vec::new();
    for line in text.lines().skip(1) {
        let mut it = line.split(',');
        let (Some(t), Some(r)) = (it.next(), it.next()) else {
            continue;
        };
        v.push((funding_min(t.parse::<i64>()?), r.parse::<f64>()?));
    }
    Ok(v)
}

/// Ставки файла `<name>.csv.gz` на минутах сетки панелей; нет файла — пусто (счётчик в выводе bench).
fn read_funding(dir: &Path, name: &str) -> Result<Vec<(i64, f64)>> {
    let f = dir.join(format!("{name}.csv.gz"));
    if !f.exists() {
        return Ok(Vec::new());
    }
    let out = Command::new("gzip")
        .arg("-dc")
        .arg(&f)
        .output()
        .context("gzip -dc")?;
    if !out.status.success() {
        bail!("gzip -dc {}: ненулевой код", f.display());
    }
    parse_funding(&String::from_utf8_lossy(&out.stdout))
}

fn load(dir: &Path, n: usize, fund: Option<&Path>) -> Result<Vec<Loaded>> {
    let mut v = Vec::new();
    for f in sorted(dir, ".sap")?.into_iter().take(n) {
        let s = read_series(&mut BufReader::with_capacity(1 << 20, File::open(&f)?))
            .with_context(|| f.display().to_string())?;
        let name = f.file_stem().unwrap().to_string_lossy().into_owned();
        let ln = s.ln_close();
        let fund = match fund {
            Some(d) => read_funding(d, &name)?,
            None => Vec::new(),
        };
        v.push(Loaded { name, s, ln, fund });
    }
    Ok(v)
}

/// Задание: Y — индекс, X — индексы компонент (по ним и из какого набора), набор Y и набор X.
struct Job {
    y: usize,
    x: Vec<usize>,
}

/// Общий спан набора рядов: первая минута и срезы `[from, from+len)` в координатах каждого.
fn common(ser: &[&Series]) -> Option<(i64, Vec<usize>, usize)> {
    let lo = ser.iter().map(|s| s.first_min).max()?;
    let hi = ser.iter().map(|s| s.last_min()).min()?;
    if hi < lo {
        return None;
    }
    Some((
        lo,
        ser.iter().map(|s| (lo - s.first_min) as usize).collect(),
        (hi - lo + 1) as usize,
    ))
}

fn run_job(
    a: &BenchArgs,
    ys: &[Loaded],
    xs: &[Loaded],
    j: &Job,
) -> Option<(Counters, usize, Drift, u64)> {
    let y = &ys[j.y];
    let xv: Vec<&Loaded> = j.x.iter().map(|&i| &xs[i]).collect();
    let mut all: Vec<&Series> = vec![&y.s];
    all.extend(xv.iter().map(|x| &x.s));
    let (first, off, len) = common(&all)?;
    if len < a.min_span {
        return None;
    }
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
        first_min: first,
        ly: &y.ln[off[0]..off[0] + len],
        lx,
        oy: &y.s.open[off[0]..off[0] + len],
        ox: &ox,
        wx: &wx,
        fund_y: &y.fund,
        fund_x: &fx,
    };
    let mut dr = Drift::default();
    let mut h = HashSink(HashSink::SEED);
    let c = match (a.engine, a.digest) {
        (Engine::Kernel, false) => run_pair(&inp, &a.p, &mut ()),
        (Engine::Kernel, true) => run_pair(&inp, &a.p, &mut h),
        (Engine::Reference, false) => reference::run_pair(&inp, &a.p, &mut ()),
        (Engine::Reference, true) => reference::run_pair(&inp, &a.p, &mut h),
        (Engine::Drift, _) => {
            dr = drift(&inp, &a.p);
            Counters::default()
        }
    };
    h.counters(&c);
    Some((c, len, dr, h.0))
}

fn bench(a: &BenchArgs) -> Result<()> {
    let t_load = Instant::now();
    let fund_on = matches!(a.kind, Kind::FundingPp | Kind::FundingPs);
    if fund_on && a.funding_y.is_none() {
        bail!("funding-* требует --funding-y");
    }
    if matches!(a.kind, Kind::FundingPp) && a.funding_x.is_none() {
        bail!("funding-pp требует --funding-x");
    }
    let ys = load(a.panels, a.symbols, a.funding_y)?;
    let xs_b;
    let xs: &[Loaded] = match (a.kind.two_panels(), a.panels_b) {
        (true, Some(b)) => {
            xs_b = load(b, usize::MAX, a.funding_x)?;
            &xs_b
        }
        (true, None) => bail!("cross / funding-* требует --panels-b"),
        _ => &ys,
    };
    let mut jobs: Vec<Job> = Vec::new();
    match a.kind {
        Kind::Pair => {
            'o: for i in 0..ys.len() {
                for j in i + 1..ys.len() {
                    if jobs.len() >= a.max_jobs {
                        break 'o;
                    }
                    jobs.push(Job { y: i, x: vec![j] });
                }
            }
        }
        Kind::Cross | Kind::FundingPp | Kind::FundingPs => {
            for (i, y) in ys.iter().enumerate() {
                if let Some(j) = xs.iter().position(|x| x.name == y.name) {
                    if jobs.len() < a.max_jobs {
                        jobs.push(Job { y: i, x: vec![j] });
                    }
                }
            }
        }
        Kind::Basket => {
            if a.basket_k == 0 || ys.len() <= a.basket_k {
                bail!("basket: нужно --basket-k ≥ 1 и символов больше K");
            }
            for i in 0..ys.len().min(a.max_jobs) {
                jobs.push(Job {
                    y: i,
                    x: (1..=a.basket_k).map(|d| (i + d) % ys.len()).collect(),
                });
            }
        }
    }
    let load_s = t_load.elapsed().as_secs_f64();
    let fund_rows: usize = ys.iter().map(|l| l.fund.len()).sum::<usize>()
        + if a.kind.two_panels() {
            xs.iter().map(|l| l.fund.len()).sum()
        } else {
            0
        };
    let next = AtomicUsize::new(0);
    let acc = Mutex::new((0u64, 0u64, 0u64, 0u64)); // заданий, минут, сделок, пропусков
    let dacc = Mutex::new(Drift::default());
    let hacc = AtomicU64::new(0);
    let t0 = Instant::now();
    std::thread::scope(|s| {
        for _ in 0..a.threads.max(1) {
            s.spawn(|| {
                let (mut n, mut rows, mut tr, mut gaps) = (0u64, 0u64, 0u64, 0u64);
                let mut dr = Drift::default();
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    let Some(j) = jobs.get(i) else { break };
                    if let Some((c, len, d, h)) = run_job(a, &ys, xs, j) {
                        // Хеш заданий складывается по номеру задания: порядок потоков результат не меняет.
                        hacc.fetch_add(
                            h ^ (i as u64).wrapping_mul(0x9e37_79b9_7f4a_7c15),
                            Ordering::Relaxed,
                        );
                        dr.add(&d);
                        n += 1;
                        rows += len as u64;
                        tr += c.trades;
                        gaps += c.gap_minutes;
                    }
                }
                let mut g = acc.lock().unwrap();
                g.0 += n;
                g.1 += rows;
                g.2 += tr;
                g.3 += gaps;
                dacc.lock().unwrap().add(&dr);
            });
        }
    });
    let (n, rows, tr, gaps) = acc.into_inner().unwrap();
    let wall = t0.elapsed().as_secs_f64();
    if matches!(a.engine, Engine::Drift) {
        let d = dacc.into_inner().unwrap();
        println!(
            "drift kind={:?} window={} refresh={}: заданий {} из {}, минут z сравнено {}, минут с разной конечностью z {}, max|Δz| {:.3e}, сделок ядро {} / референс {}, разошлось сделок {}, max|Δnet_bps| по совпавшим {:.3e}, {:.1} с",
            a.kind, a.p.window, a.p.refresh, n, jobs.len(), d.z_cmp, d.z_nan_diff, d.max_dz, d.trades_k, d.trades_r, d.trades_diff, d.max_dnet, wall
        );
        return Ok(());
    }
    println!(
        "bench kind={:?} engine={:?} threads={} window={} refresh={}: заданий {} из {}, минут {}, пропусков {}, сделок {}, счёт {:.3} с (загрузка {:.1} с, ставок фандинга {}), {:.3e} строк/с, {:.1} пар/с",
        a.kind, a.engine, a.threads, a.p.window, a.p.refresh, n, jobs.len(), rows, gaps, tr, wall, load_s, fund_rows,
        rows as f64 / wall, n as f64 / wall
    );
    if a.digest {
        println!("digest {:016x}", hacc.load(Ordering::Relaxed));
    }
    Ok(())
}
