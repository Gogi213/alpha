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
//!
//! Допил TK-493 (стат-арб на 9 биржах). Без новых флагов вывод побайтно прежний (суточный csv, сделки).
//! - `--delay-x N` (C1), `--rule z|funding --funding-k K --hold-payments H` (D3) — как в `bench`; одни на весь прогон.
//!   Многоклеточный проход их не умеет (`multi.rs` отклоняет), поэтому при `--cells` такие клетки идут одноклеточным
//!   `run_pair` над теми же панелями: результат равен отдельным прогонам клеток. Столбец `fill_x` (исполнение ноги X)
//!   пишется в сделки только при `--delay-x ≥ 1` — иначе он равен `fill_in`, а заголовок прежний.
//! - Фандинг: pair/basket — ставки обеих ног из `--funding-y`; cross — Y из `--funding-y`, X из `--funding-x`
//!   (оба или ни одного); funding-pp — оба обязательны; funding-ps — только `--funding-y` (спот-нога без фандинга).
//!   Минуты выплат и панелей в одной эпохе (E-010: `cli::funding_min`). Нет файла ставок у символа — нога без
//!   фандинга; число таких ног печатается строкой «фандинг: …», чтобы опечатка в каталоге не давала молчаливый ноль.
//! - Две биржи: `--panels`/`--funding-y` — биржа ноги Y, `--panels-b`/`--funding-x` — биржа ноги X; имена символов
//!   задают `y,x` задания, к Binance/Bybit/Gate ничего не привязано.
//! - `--from-day D1 --to-day D2` обрезают окно каждого задания до `[D1, D2)` (общая нарезка заданий вокруг дыр
//!   биржи); задание с пустым окном считается «нет спана». `exclude_window` из `gate_data.py`/P-32-params ядро НЕ
//!   применяет (это только запись в params): дыру целой биржи режут заданиями по суткам (`from_day/to_day` в файле
//!   заданий или эти флаги: два прогона «до дыры» и «после дыры»), либо NaN-маской панелей до счёта (в `convert` такой
//!   маски нет). Позиция, не закрытая к концу сегмента, — `open_at_end` сегмента (оценка — `--mtm-open`).
//! - `--cells-out <csv>`: по клетке `jobs,done,missing,no_span,trades,pre_window,entries,entry_deferred,exit_deferred,
//!   x_deferred,gap_minutes,minutes_in_pos,open_at_end,open_pre_window` и столбцы оценки `mtm_*`.
//! - `--mtm-open` (по умолчанию выкл., требует `--cells-out`): позиция, оставшаяся открытой на конце окна задания,
//!   оценивается по последнему валидному close каждой ноги (`mtm::value`) — граница чувствительности, а не результат;
//!   в суточный csv и сделки она не попадает. Без флага столбцы `mtm_*` — `nan`.

use super::cli::{common, load_names, Kind, Loaded};
use super::kernel::{
    run_pair, BetaMode, Counters, LegKind, OpenPos, PairInput, Params, Rule, Sink, Trade,
};
use super::mtm::{self, Mtm, OpenSnap};
use super::multi::run_pair_multi;
use anyhow::{bail, Context, Result};
use clap::{Args, ValueEnum};
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
    /// C1: задержка входа ноги X после ноги Y, минут; 0 — обе ноги на одном open (как раньше). Одна на весь прогон;
    /// с ней клетки `--cells` идут одноклеточным проходом. В сделки добавляется столбец `fill_x`.
    #[arg(long, default_value_t = 0)]
    delay_x: usize,
    /// Правило входа: z — по z-скору (как раньше), funding — D3 по уровню ставки ноги Y. Без флага — funding, если задан
    /// `--funding-k` (как в bench), иначе z. При funding пороги z клетки не используются, но обязательны в формате клетки.
    #[arg(long, value_enum)]
    rule: Option<RuleArg>,
    /// D3: k последних выплат ноги Y для уровня ставки (требует `--funding-y`).
    #[arg(long, default_value_t = 0)]
    funding_k: usize,
    /// D3: ожидаемое удержание в выплатах (обязательно при funding).
    #[arg(long, default_value_t = 0)]
    hold_payments: usize,
    /// Оценить по рынку позиции, оставшиеся открытыми на конце окна задания (последний валидный close каждой ноги).
    /// Граница чувствительности, не результат; пишется только в `--cells-out`.
    #[arg(long, default_value_t = false)]
    mtm_open: bool,
    /// Сводка по клетке: счётчики (сделки, входы, отложенные входы/выходы, открытые на конце) и оценка `--mtm-open`.
    #[arg(long)]
    cells_out: Option<PathBuf>,
    /// Обрезать окна всех заданий снизу (сутки, включительно); см. модульную документацию про дыры биржи.
    #[arg(long)]
    from_day: Option<i64>,
    /// Обрезать окна всех заданий сверху (сутки, не включая).
    #[arg(long)]
    to_day: Option<i64>,
}

/// Правило входа и выхода (`--rule`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum RuleArg {
    /// По z-скору спреда: пороги клетки.
    Z,
    /// D3: по уровню ставки фандинга ноги Y.
    Funding,
}

/// Режим прогона, общий для всех клеток: задержка ноги X и правило.
#[derive(Debug, Clone, Copy)]
struct Mode {
    delay_x: usize,
    rule: Rule,
}

/// Проверка флагов и режим. Ошибка — до чтения панелей.
fn check_args(a: &RunArgs) -> Result<Mode> {
    if a.kind.two_panels() && a.panels_b.is_none() {
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
    match a.kind {
        Kind::Pair | Kind::Basket if a.funding_x.is_some() => {
            bail!("pair / basket: ставки обеих ног читаются из --funding-y, --funding-x не нужен")
        }
        Kind::FundingPs if a.funding_x.is_some() => {
            bail!("funding-ps: нога X — спот без фандинга, --funding-x не нужен")
        }
        Kind::Cross if a.funding_y.is_some() != a.funding_x.is_some() => {
            bail!("cross: ставки либо у обеих ног (--funding-y и --funding-x), либо ни у одной")
        }
        _ => {}
    }
    if a.mtm_open && a.cells_out.is_none() {
        bail!("--mtm-open пишет оценку только в --cells-out: укажите его");
    }
    if let (Some(f), Some(t)) = (a.from_day, a.to_day) {
        if t <= f {
            bail!("--to-day {t} не больше --from-day {f}");
        }
    }
    let funding = match a.rule {
        Some(RuleArg::Funding) => true,
        Some(RuleArg::Z) => false,
        None => a.funding_k > 0,
    };
    let rule = if funding {
        if a.funding_k == 0 || a.hold_payments == 0 {
            bail!("правило funding требует --funding-k ≥ 1 и --hold-payments ≥ 1");
        }
        if a.funding_y.is_none() {
            bail!("правило funding требует --funding-y");
        }
        Rule::Funding {
            k: a.funding_k,
            hold_payments: a.hold_payments,
        }
    } else {
        if a.funding_k != 0 || a.hold_payments != 0 {
            bail!("--funding-k и --hold-payments относятся к правилу funding (--rule funding)");
        }
        Rule::Z
    };
    Ok(Mode {
        delay_x: a.delay_x,
        rule,
    })
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

/// Обрезать окна заданий до `[from, to)` (флаги `--from-day/--to-day`); пустое окно остаётся в списке: номера
/// заданий (`--trade-jobs`, столбец `job`) не сдвигаются, такое задание считается «нет спана».
fn clamp_jobs(jobs: &mut [Job], from: Option<i64>, to: Option<i64>) {
    for j in jobs {
        if let Some(f) = from {
            j.from_day = j.from_day.max(f);
        }
        if let Some(t) = to {
            j.to_day = j.to_day.min(t);
        }
    }
}

/// Сутки закрытия → net, gross, fee, funding, число сделок.
type Daily = BTreeMap<i64, [f64; 5]>;

#[derive(Default)]
struct JobOut {
    daily: Daily,
    c: Counters,
    pre_window: u64,
    trades: Vec<Trade>,
    /// Позиция, открытая на конце окна, вошла до `from_day` (в разогреве): сделку с таким входом `tally` отбрасывает,
    /// открытую позицию тоже не оцениваем.
    open_pre: u64,
    /// Оценка позиции, открытой на конце окна (`--mtm-open`).
    mtm: Option<Mtm>,
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

/// Неизменное на прогон: панели ног Y и X, минимум спана, оценка открытых позиций.
struct Env<'a> {
    min_span: usize,
    mtm: bool,
    ys: &'a Panels,
    xs: &'a Panels,
}

/// Закрытия окна задания: индекс `i` ↔ минута `first_min + i`, как в `PairInput`.
struct Closes<'a> {
    y: &'a [f64],
    x: Vec<&'a [f64]>,
}

/// Срез `[from, from + len)`; за границей — пусто (окна `with_input` внутри ряда, до сюда не доходит).
fn slice_at(c: &[f64], from: usize, len: usize) -> &[f64] {
    c.get(from..from + len).unwrap_or(&[])
}

/// Собрать вход пары для окна оценки `window` и вызвать `f`.
fn with_input<R>(
    window: usize,
    env: &Env,
    j: &Job,
    f: impl FnOnce(&PairInput, &Closes) -> R,
) -> Prep<R> {
    let Some(y) = env.ys.get(&j.y) else {
        return Prep::Missing;
    };
    let Some(xv) =
        j.x.iter()
            .map(|n| env.xs.get(n))
            .collect::<Option<Vec<&Loaded>>>()
    else {
        return Prep::Missing;
    };
    if j.to_day <= j.from_day {
        return Prep::NoSpan; // пустое окно после --from-day/--to-day
    }
    let mut all = vec![&y.s];
    all.extend(xv.iter().map(|x| &x.s));
    let Some((first, off, len)) = common(&all) else {
        return Prep::NoSpan;
    };
    let (from_min, to_min) = (j.from_day * MIN_DAY, j.to_day * MIN_DAY);
    let lo = first.max(from_min - window as i64);
    let hi = (first + len as i64 - 1).min(to_min - 1);
    if hi < lo || ((hi - lo + 1) as usize) < env.min_span {
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
    let cl = Closes {
        y: slice_at(&y.s.close, off.first().copied().unwrap_or(0), len),
        x: xv
            .iter()
            .zip(off.iter().skip(1))
            .map(|(x, &o)| slice_at(&x.s.close, o, len))
            .collect(),
    };
    Prep::Ready(f(&inp, &cl))
}

/// Приёмник прогона: сделки и позиция, оставшаяся открытой на конце окна.
#[derive(Default)]
struct RunSink {
    trades: Vec<Trade>,
    open: Option<OpenSnap>,
}

impl Sink for RunSink {
    fn trade(&mut self, t: &Trade) {
        self.trades.push(*t);
    }

    fn open_end(&mut self, pos: &OpenPos) {
        self.open = Some(OpenSnap::of(pos));
    }
}

/// Оценка открытой позиции (`--mtm-open`); `None` — не оценивать.
type Mark<'a> = Option<&'a dyn Fn(&OpenSnap) -> Mtm>;

/// Сделки клетки → суточные суммы по суткам закрытия; сделки с входом до `from_min` отбрасываются и считаются.
fn tally(sink: RunSink, c: Counters, from_min: i64, keep: bool, mark: Mark) -> JobOut {
    let mut o = JobOut {
        c,
        ..JobOut::default()
    };
    if let Some(op) = &sink.open {
        if op.fill_in < from_min {
            o.open_pre += 1;
        } else {
            o.mtm = mark.map(|m| m(op));
        }
    }
    for t in sink.trades {
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
fn run_job(p: &Params, env: &Env, j: &Job, keep: bool) -> Outcome {
    let from_min = j.from_day * MIN_DAY;
    outcome(with_input(p.window, env, j, |inp, cl| {
        let mut sink = RunSink::default();
        let c = run_pair(inp, p, &mut sink);
        let val = |o: &OpenSnap| mtm::value(p, o, inp.first_min, cl.y, &cl.x);
        let mark: Mark = env.mtm.then_some(&val as &dyn Fn(&OpenSnap) -> Mtm);
        tally(sink, c, from_min, keep, mark)
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

/// Клетку умеет многоклеточный проход (`multi.rs`): без задержки ноги X и с правилом z.
fn multi_ok(p: &Params) -> bool {
    p.delay_x == 0 && p.rule == Rule::Z
}

/// Все клетки одного задания: по проходу `run_pair_multi` на группу, а клетки с `delay_x` / правилом funding
/// (их multi отклоняет) — одноклеточным `run_pair` на тех же входах. Исход `i` — клетка `i` файла.
fn run_job_cells(
    cells: &[Cell],
    groups: &[Vec<usize>],
    env: &Env,
    j: &Job,
    keep: bool,
) -> Vec<Outcome> {
    let from_min = j.from_day * MIN_DAY;
    let mut out: Vec<Option<Outcome>> = cells.iter().map(|_| None).collect();
    for g in groups {
        let ps: Vec<Params> = g.iter().map(|&i| cells[i].p).collect();
        let prep = with_input(ps[0].window, env, j, |inp, cl| {
            let mut sinks: Vec<RunSink> = g.iter().map(|_| RunSink::default()).collect();
            let cs: Vec<Counters> = if ps.iter().all(multi_ok) {
                run_pair_multi(inp, &ps, &mut sinks).expect("группа клеток согласована")
            } else {
                ps.iter()
                    .zip(sinks.iter_mut())
                    .map(|(p, s)| run_pair(inp, p, s))
                    .collect()
            };
            sinks
                .into_iter()
                .zip(cs)
                .zip(&ps)
                .map(|((sink, c), p)| {
                    let val = |o: &OpenSnap| mtm::value(p, o, inp.first_min, cl.y, &cl.x);
                    let mark: Mark = env.mtm.then_some(&val as &dyn Fn(&OpenSnap) -> Mtm);
                    tally(sink, c, from_min, keep, mark)
                })
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

/// Параметры клетки; стороны ног и комиссии задаёт `kind`, задержку ноги X и правило — `mode`.
#[allow(clippy::too_many_arguments)]
fn cell_params(
    kind: Kind,
    mode: Mode,
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
        delay_x: mode.delay_x,
        rule: mode.rule,
    }
}

const CELLS_HEADER: &str = "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh";

fn read_cells(path: &Path, kind: Kind, mode: Mode) -> Result<Vec<Cell>> {
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
            mode,
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

fn one_cell(a: &RunArgs, mode: Mode) -> Result<Cell> {
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
            mode,
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
    open_pre: u64,
    /// Оценённых позиций, открытых на конце окна, и суммы их net / gross / fee / funding, bps (`--mtm-open`).
    mtm_n: u64,
    mtm: [f64; 4],
    mtm_stale_max: i64,
}

impl Agg {
    fn add(&mut self, job: usize, o: Outcome) {
        match o {
            Outcome::Missing => self.missing += 1,
            Outcome::NoSpan => self.no_span += 1,
            Outcome::Done(o) => {
                self.done += 1;
                self.pre += o.pre_window;
                self.open_pre += o.open_pre;
                self.c.entries += o.c.entries;
                self.c.entry_deferred += o.c.entry_deferred;
                self.c.exit_deferred += o.c.exit_deferred;
                self.c.x_deferred += o.c.x_deferred;
                self.c.trades += o.c.trades;
                self.c.minutes_in_pos += o.c.minutes_in_pos;
                self.c.open_at_end += o.c.open_at_end;
                self.c.gap_minutes += o.c.gap_minutes;
                if let Some(m) = o.mtm {
                    self.mtm_n += 1;
                    let parts = [m.net_bps, m.gross_bps, m.fee_bps, m.funding_bps];
                    for (acc, x) in self.mtm.iter_mut().zip(parts) {
                        *acc += x;
                    }
                    self.mtm_stale_max = self.mtm_stale_max.max(m.stale_min);
                }
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

/// Имена символов заданий с непустым окном (после `--from-day/--to-day` пустые панелей не читают).
fn uniq(jobs: &[Job], f: impl Fn(&Job) -> Vec<&String>) -> Vec<String> {
    let mut v: Vec<String> = jobs
        .iter()
        .filter(|j| j.to_day > j.from_day)
        .flat_map(f)
        .cloned()
        .collect();
    v.sort();
    v.dedup();
    v
}

/// Суточный csv: `cell,day,net_bps,gross_bps,fee_bps,funding_bps,trades` по суткам закрытия.
fn write_daily(path: &Path, cells: &[Cell], aggs: &[Agg]) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(w, "cell,day,net_bps,gross_bps,fee_bps,funding_bps,trades")?;
    for (cell, agg) in cells.iter().zip(aggs) {
        for (d, v) in &agg.daily {
            writeln!(
                w,
                "{},{},{},{},{},{},{}",
                cell.label, d, v[0], v[1], v[2], v[3], v[4] as u64
            )?;
        }
    }
    w.flush()?;
    Ok(())
}

/// Сделки выбранных заданий. Столбец `fill_x` — только при `--delay-x ≥ 1` (иначе он равен `fill_in`, заголовок прежний).
fn write_trades(
    path: &Path,
    cells: &[Cell],
    aggs: &[Agg],
    jobs: &[Job],
    fill_x: bool,
) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(
        w,
        "cell,job,y,x,dir,sig_in,fill_in,{}sig_out,fill_out,gross_bps,fee_bps,funding_bps,net_bps",
        if fill_x { "fill_x," } else { "" }
    )?;
    for (cell, agg) in cells.iter().zip(aggs) {
        for (job, t) in &agg.trades {
            let j = &jobs[*job];
            write!(
                w,
                "{},{},{},{},{},{},{},",
                cell.label,
                job,
                j.y,
                j.x.join("+"),
                t.dir,
                t.sig_in,
                t.fill_in
            )?;
            if fill_x {
                write!(w, "{},", t.fill_x)?;
            }
            writeln!(
                w,
                "{},{},{},{},{},{}",
                t.sig_out, t.fill_out, t.gross_bps, t.fee_bps, t.funding_bps, t.net_bps
            )?;
        }
    }
    w.flush()?;
    Ok(())
}

const CELLS_OUT_COLUMNS: [&str; 21] = [
    "cell",
    "jobs",
    "done",
    "missing",
    "no_span",
    "trades",
    "pre_window",
    "entries",
    "entry_deferred",
    "exit_deferred",
    "x_deferred",
    "gap_minutes",
    "minutes_in_pos",
    "open_at_end",
    "open_pre_window",
    "mtm_n",
    "mtm_net_bps",
    "mtm_gross_bps",
    "mtm_fee_bps",
    "mtm_funding_bps",
    "mtm_stale_max_min",
];

/// Сводка по клетке (`--cells-out`). Столбцы `mtm_*` без `--mtm-open` — `nan`: «нет оценки», а не «ноль».
fn write_cells_out(
    path: &Path,
    cells: &[Cell],
    aggs: &[Agg],
    njobs: usize,
    mtm_on: bool,
) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(w, "{}", CELLS_OUT_COLUMNS.join(","))?;
    for (cell, g) in cells.iter().zip(aggs) {
        let c = &g.c;
        let mut f: Vec<String> = vec![cell.label.clone(), njobs.to_string()];
        f.extend(
            [
                g.done,
                g.missing,
                g.no_span,
                c.trades,
                g.pre,
                c.entries,
                c.entry_deferred,
                c.exit_deferred,
                c.x_deferred,
                c.gap_minutes,
                c.minutes_in_pos,
                c.open_at_end,
                g.open_pre,
            ]
            .map(|v| v.to_string()),
        );
        if mtm_on {
            f.push(g.mtm_n.to_string());
            f.extend(g.mtm.map(|v| v.to_string()));
            f.push(g.mtm_stale_max.to_string());
        } else {
            f.extend(["nan"; 6].map(String::from));
        }
        writeln!(w, "{}", f.join(","))?;
    }
    w.flush()?;
    Ok(())
}

/// Сколько ног со ставками фандинга: нет файла ставок у символа — нога без фандинга (молчаливый ноль), поэтому видно.
fn funding_line(a: &RunArgs, ys: &Panels, xs: &Panels) -> Option<String> {
    let legs = |m: &Panels| {
        format!(
            "{}/{}",
            m.values().filter(|l| !l.fund.is_empty()).count(),
            m.len()
        )
    };
    if a.funding_y.is_none() && a.funding_x.is_none() {
        return None;
    }
    if !a.kind.two_panels() {
        return Some(format!("фандинг: ног со ставками {}", legs(ys)));
    }
    let mut parts = Vec::new();
    if a.funding_y.is_some() {
        parts.push(format!("Y {}", legs(ys)));
    }
    if a.funding_x.is_some() {
        parts.push(format!("X {}", legs(xs)));
    }
    Some(format!("фандинг: ног со ставками {}", parts.join(", ")))
}

pub fn run(a: &RunArgs) -> Result<()> {
    let t0 = Instant::now();
    let mode = check_args(a)?;
    let two = a.kind.two_panels();
    let multi = a.cells.is_some();
    let cells = match &a.cells {
        Some(f) => read_cells(f, a.kind, mode)?,
        None => vec![one_cell(a, mode)?],
    };
    let groups = group_cells(&cells);
    let mut jobs = read_jobs(&a.jobs)?;
    clamp_jobs(&mut jobs, a.from_day, a.to_day);
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
    let env = Env {
        min_span: a.min_span,
        mtm: a.mtm_open,
        ys: &ys,
        xs,
    };
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
                    run_job_cells(&cells, &groups, &env, j, keep)
                } else {
                    vec![run_job(&cells[0].p, &env, j, keep)]
                };
                merge.lock().unwrap().put(i, outs);
            });
        }
    });
    let run_s = t0.elapsed().as_secs_f64() - load_s;
    let aggs = merge.into_inner().unwrap().aggs;
    write_daily(&a.daily_out, &cells, &aggs)?;
    if let Some(f) = &a.trades_out {
        write_trades(f, &cells, &aggs, &jobs, mode.delay_x > 0)?;
    }
    if let Some(f) = &a.cells_out {
        write_cells_out(f, &cells, &aggs, jobs.len(), a.mtm_open)?;
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
    if let Some(l) = funding_line(a, &ys, xs) {
        println!("{l}");
    }
    Ok(())
}
