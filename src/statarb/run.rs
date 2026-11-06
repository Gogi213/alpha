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
//!   в суточный csv и сделки она не попадает. Без флага столбцы `mtm_*` — `nan`. `--open-out <csv>` (требует
//!   `--mtm-open` и `--trade-jobs`): по строке на такую позицию заданий из `--trade-jobs` — вход, оценка, давность
//!   последнего close (для ручной сверки, G8b).
//!
//! Правки по аудиту TK-493 (волна 3, F-01/F-02/F-03/P-02). Без новых флагов вывод побайтно прежний.
//! - `--rearm` (по умолчанию выкл., правило z): после выхода по стопу и по `max_hold` вход в ту же сторону разрешён, только когда
//!   `|z| < entry_z` (`Params::rearm`, ядро, многоклеточный проход и reference — одно правило). Без флага стоп закрывает и тут же
//!   открывает заново: z на следующей минуте всё ещё за порогом входа.
//! - `--trades-ext`: в конец `--trades-out` дописываются столбцы `exit_reason` (rule / stop / timeout) и `beta_in` (β на входе).
//! - `--ext-out <csv>`: по клетке счётчики F-01 (доля минут с определённым z, минуты в позиции с z = NaN, минуты без права
//!   входа из-за неполного окна), разбивка выходов по причинам и `open_end`, F-03 (bps номинала Y и bps валового номинала
//!   `1 + |β|·Σw`, перцентили β на входе — наибольший ранг, доля сделок с β < 0), `rearm_blocked`. Сделки — те же, что в
//!   суточном csv (вход не раньше `from_day`).
//! - `--job-day-out <csv>`: строка на (клетка, задание, сутки): net, gross, fee, funding, сделок по суткам закрытия и `active`
//!   (z определён хотя бы в одной минуте суток внутри окна задания; `Sink::z`). Сумма по заданиям равна суточному csv клетки.
//!
//! Волна 5 TK-493 (D5(ii), аудит M3-04, L1-03, L2-03). Без новых флагов вывод побайтно прежний.
//! - `--no-entry-before-from` (по умолчанию выкл.): вход не исполняется раньше `from_day · 1440` задания (`Params::entry_from`,
//!   ядро, многоклеточный проход и reference — одно правило; сигнал допустим с минуты `from_min − 1`). Окно z и кольцо ставок
//!   фандинга греются как раньше — запрещены только решения о входе (D2: первая сделка пары больше не открывается в разогреве
//!   и не отбрасывается как `pre_window`). Минуты с подавленным сигналом — `warmup_blocked`; столбец дописывается в `--cells-out`
//!   (и в `--job-ext-out`) только с флагом, заголовки без флага прежние.
//! - `--job-ext-out <csv>`: строка на (клетка, задание) со счётчиками ядра по заданию: `status` (done / missing / no_span),
//!   `eval_minutes`, `z_def_minutes`, `minutes_in_pos`, `pos_z_nan_minutes`, `noentry_window_minutes`, `warmup_blocked`, `entries`, `trades`,
//!   `pre_window`, `pre_window_net_bps` (Σ net отброшенных сделок), `open_at_end`, `open_pre_window` и оценка открытой позиции задания `mtm_*` (`nan` без `--mtm-open`, 0 без позиции).
//!   Для D2 (`Rule::Funding`) счётчики z относятся к окну z (60 мин), а решение принимает кольцо ставок: `z_def_minutes` и
//!   `pos_z_nan_minutes` там не показатель (аудит L1-04), активность пары-суток считает агрегатор по панелям.

use super::cli::{common, load_names, Kind, Loaded};
use super::kernel::{
    run_pair, BetaMode, Counters, ExitReason, LegKind, OpenPos, PairInput, Params, Rule, Sink,
    Trade,
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
    /// Открытые на конце окна позиции заданий из `--trade-jobs` с оценкой, по строке на позицию (требует `--mtm-open`).
    #[arg(long)]
    open_out: Option<PathBuf>,
    /// Обрезать окна всех заданий снизу (сутки, включительно); см. модульную документацию про дыры биржи.
    #[arg(long)]
    from_day: Option<i64>,
    /// Обрезать окна всех заданий сверху (сутки, не включая).
    #[arg(long)]
    to_day: Option<i64>,
    /// F-02: после выхода по стопу и по `max_hold` вход в ту же сторону разрешён, только когда |z| < entry_z (по умолчанию
    /// выкл.: стоп закрывает и тут же открывает заново). Только правило z.
    #[arg(long, default_value_t = false)]
    rearm: bool,
    /// В конец `--trades-out` дописать столбцы `exit_reason,beta_in` (прежние столбцы не меняются).
    #[arg(long, default_value_t = false)]
    trades_ext: bool,
    /// Сводка по клетке с новыми счётчиками (F-01, F-02, F-03); см. модульную документацию.
    #[arg(long)]
    ext_out: Option<PathBuf>,
    /// Строка на (клетка, задание, сутки): net, gross, fee, funding, сделок и `active` (P-02, M-07).
    #[arg(long)]
    job_day_out: Option<PathBuf>,
    /// D5(ii) (TK-493 w5): не исполнять вход раньше `from_day · 1440` задания — разогрев окна z и кольца ставок идёт как
    /// обычно, решения о входе запрещены (по умолчанию выкл.). Подавленные минуты — `warmup_blocked` в `--cells-out`.
    #[arg(long, default_value_t = false)]
    no_entry_before_from: bool,
    /// Строка на (клетка, задание): счётчики ядра по заданию, `pre_window`, оценка открытой позиции (аудит L1-03, M3-04).
    #[arg(long)]
    job_ext_out: Option<PathBuf>,
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
    rearm: bool,
}

/// `delay_x = usize::MAX` совпадает с признаком «нога X не ждёт» в ядре (`NO_DUE`) — зарезервировано.
const DELAY_X_RESERVED: usize = usize::MAX;

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
    if a.delay_x == DELAY_X_RESERVED {
        bail!("--delay-x {DELAY_X_RESERVED} зарезервирован ядром (признак «нога X не ждёт»)");
    }
    if a.mtm_open && a.cells_out.is_none() && a.job_ext_out.is_none() {
        bail!("--mtm-open пишет оценку только в --cells-out и --job-ext-out: укажите один из них");
    }
    if a.open_out.is_some() && (!a.mtm_open || a.trade_jobs.is_empty()) {
        bail!("--open-out требует --mtm-open и --trade-jobs");
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
    if a.rearm && !matches!(rule, Rule::Z) {
        bail!("--rearm относится к правилу z (полоса входа |z| < entry_z), с правилом funding не сочетается");
    }
    if a.trades_ext && a.trades_out.is_none() {
        bail!("--trades-ext дописывает столбцы в --trades-out: укажите его");
    }
    Ok(Mode {
        delay_x: a.delay_x,
        rule,
        rearm: a.rearm,
    })
}

struct Job {
    y: String,
    x: Vec<String>,
    from_day: i64,
    to_day: i64,
}

const JOBS_HEADER: &str = "y,x,from_day,to_day";

/// Первая строка файла — заголовок ровно такой, как в формате (аудит L2-03): иначе `skip(1)` молча съедал бы первую строку данных.
fn check_header(path: &Path, txt: &str, want: &str) -> Result<()> {
    let first = txt
        .lines()
        .next()
        .unwrap_or("")
        .trim_start_matches('\u{feff}')
        .trim();
    if first != want {
        bail!(
            "{}: первая строка должна быть заголовком «{want}», а не «{first}»",
            path.display()
        );
    }
    Ok(())
}

fn read_jobs(path: &Path) -> Result<Vec<Job>> {
    let txt = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    check_header(path, &txt, JOBS_HEADER)?;
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

/// Статистика сделок для `--ext-out` (сделки с входом не раньше `from_day`, как в суточном csv): выходы по причинам,
/// net в bps номинала Y и в bps валового номинала `1 + |β|·Σw`, β на входе.
#[derive(Default)]
struct ExtStats {
    n: u64,
    /// Выходы по причинам.
    n_rule: u64,
    n_stop: u64,
    n_timeout: u64,
    net_y: f64,
    net_gross: f64,
    notional: f64,
    beta_neg: u64,
    betas: Vec<f64>,
}

impl ExtStats {
    fn add(&mut self, t: &Trade, wsum: f64) {
        let notional = 1.0 + t.beta.abs() * wsum;
        self.n += 1;
        match t.reason {
            ExitReason::Rule => self.n_rule += 1,
            ExitReason::Stop => self.n_stop += 1,
            ExitReason::Timeout => self.n_timeout += 1,
        }
        self.net_y += t.net_bps;
        self.net_gross += t.net_bps / notional;
        self.notional += notional;
        if t.beta < 0.0 {
            self.beta_neg += 1;
        }
        self.betas.push(t.beta);
    }

    /// Слияние в порядке номера задания: суммы f64 не зависят от потоков.
    fn merge(&mut self, o: ExtStats) {
        self.n += o.n;
        self.n_rule += o.n_rule;
        self.n_stop += o.n_stop;
        self.n_timeout += o.n_timeout;
        self.net_y += o.net_y;
        self.net_gross += o.net_gross;
        self.notional += o.notional;
        self.beta_neg += o.beta_neg;
        self.betas.extend(o.betas);
    }
}

/// Что собирать сверх суточных сумм (новые выводы TK-493 w3); всё выключено — работа прежняя.
#[derive(Clone, Copy)]
struct Extra {
    ext: bool,
    job_day: bool,
    /// Σ весов компонент X задания (равные веса: 1).
    wsum: f64,
}

/// Сутки, в которых z определён хотя бы в одной минуте окна задания (`Sink::z`); сутки идут по возрастанию.
struct Active {
    first_min: i64,
    /// Индекс первой минуты окна задания (до неё — разогрев `window`, его сутки не считаются).
    start: usize,
    days: Vec<i64>,
}

#[derive(Default)]
struct JobOut {
    daily: Daily,
    c: Counters,
    ext: ExtStats,
    /// Сутки задания (сутки, суммы за сутки закрытия, `active`) — для `--job-day-out`.
    jd: Vec<(i64, [f64; 5], bool)>,
    pre_window: u64,
    /// Σ net отброшенных `pre_window` сделок, bps (величина потери, не только их число; `--job-ext-out`).
    pre_net_bps: f64,
    trades: Vec<Trade>,
    /// Позиция, открытая на конце окна, вошла до `from_day` (в разогреве): сделку с таким входом `tally` отбрасывает,
    /// открытую позицию тоже не оцениваем.
    open_pre: u64,
    /// Оценка позиции, открытой на конце окна (`--mtm-open`).
    mtm: Option<Mtm>,
    /// Она же вместе со входом, для заданий из `--trade-jobs` (`--open-out`).
    open: Option<(OpenSnap, Mtm)>,
}

enum Outcome {
    Missing,
    NoSpan,
    Done(Box<JobOut>),
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
    /// `--ext-out`: собирать статистику сделок.
    ext: bool,
    /// `--job-day-out`: собирать сутки заданий и `Sink::z`.
    job_day: bool,
    /// `--no-entry-before-from`: вход не раньше `from_min` задания (`Params::entry_from`).
    gate: bool,
    ys: &'a Panels,
    xs: &'a Panels,
}

impl Env<'_> {
    /// Параметры клетки на задании: с `--no-entry-before-from` в них минута `from_min` задания.
    fn gated(&self, p: &Params, from_min: i64) -> Params {
        Params {
            entry_from: self.gate.then_some(from_min),
            ..*p
        }
    }

    fn extra(&self, inp: &PairInput) -> Extra {
        Extra {
            ext: self.ext,
            job_day: self.job_day,
            wsum: inp.wx.iter().sum(),
        }
    }

    /// Приёмник прогона задания; z-хук включается только для `--job-day-out`.
    fn sink(&self, inp: &PairInput, from_min: i64) -> RunSink {
        RunSink {
            active: self.job_day.then(|| Active {
                first_min: inp.first_min,
                start: usize::try_from((from_min - inp.first_min).max(0)).unwrap_or(0),
                days: Vec::new(),
            }),
            ..RunSink::default()
        }
    }
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
    active: Option<Active>,
}

impl Sink for RunSink {
    fn trade(&mut self, t: &Trade) {
        self.trades.push(*t);
    }

    fn z(&mut self, t: usize, z: f64) {
        if let Some(a) = &mut self.active {
            if z.is_finite() && t >= a.start {
                let d = (a.first_min + t as i64).div_euclid(MIN_DAY);
                if a.days.last() != Some(&d) {
                    a.days.push(d);
                }
            }
        }
    }

    fn open_end(&mut self, pos: &OpenPos) {
        self.open = Some(OpenSnap::of(pos));
    }
}

/// Оценка открытой позиции (`--mtm-open`); `None` — не оценивать.
type Mark<'a> = Option<&'a dyn Fn(&OpenSnap) -> Mtm>;

/// Сделки клетки → суточные суммы по суткам закрытия; сделки с входом до `from_min` отбрасываются и считаются.
fn tally(
    mut sink: RunSink,
    c: Counters,
    from_min: i64,
    keep: bool,
    mark: Mark,
    extra: Extra,
) -> JobOut {
    let mut o = JobOut {
        c,
        ..JobOut::default()
    };
    let active = sink.active.take();
    if let Some(op) = &sink.open {
        if op.fill_in < from_min {
            o.open_pre += 1;
        } else {
            o.mtm = mark.map(|m| m(op));
            if keep {
                o.open = o.mtm.map(|m| (op.clone(), m));
            }
        }
    }
    for t in sink.trades {
        if t.fill_in < from_min {
            o.pre_window += 1;
            o.pre_net_bps += t.net_bps;
            continue;
        }
        let d = o.daily.entry(t.fill_out.div_euclid(MIN_DAY)).or_default();
        d[0] += t.net_bps;
        d[1] += t.gross_bps;
        d[2] += t.fee_bps;
        d[3] += t.funding_bps;
        d[4] += 1.0;
        if extra.ext {
            o.ext.add(&t, extra.wsum);
        }
        if keep {
            o.trades.push(t);
        }
    }
    if extra.job_day {
        // сутки с активностью и сутки закрытия сделок; активность без сделок — нули
        let mut days: BTreeMap<i64, bool> = o.daily.keys().map(|&d| (d, false)).collect();
        for d in active.map(|a| a.days).unwrap_or_default() {
            days.insert(d, true);
        }
        o.jd = days
            .into_iter()
            .map(|(d, a)| (d, o.daily.get(&d).copied().unwrap_or_default(), a))
            .collect();
    }
    o
}

fn outcome(p: Prep<JobOut>) -> Outcome {
    match p {
        Prep::Missing => Outcome::Missing,
        Prep::NoSpan => Outcome::NoSpan,
        Prep::Ready(o) => Outcome::Done(Box::new(o)),
    }
}

/// Одна клетка, ядро-эталон `run_pair`.
fn run_job(p: &Params, env: &Env, j: &Job, keep: bool) -> Outcome {
    let from_min = j.from_day * MIN_DAY;
    let p = &env.gated(p, from_min);
    outcome(with_input(p.window, env, j, |inp, cl| {
        let mut sink = env.sink(inp, from_min);
        let c = run_pair(inp, p, &mut sink);
        let val = |o: &OpenSnap| mtm::value(p, o, inp.first_min, cl.y, &cl.x);
        let mark: Mark = env.mtm.then_some(&val as &dyn Fn(&OpenSnap) -> Mtm);
        tally(sink, c, from_min, keep, mark, env.extra(inp))
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

/// Клетку умеет многоклеточный проход (`multi.rs`): без задержки ноги X, с правилом z и без запрета входа до `from_min`
/// (`--no-entry-before-from`: такие клетки идут одноклеточным `run_pair`, результат равен отдельным прогонам клеток).
fn multi_ok(p: &Params) -> bool {
    p.delay_x == 0 && p.rule == Rule::Z && p.entry_from.is_none()
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
        let ps: Vec<Params> = g
            .iter()
            .map(|&i| env.gated(&cells[i].p, from_min))
            .collect();
        let prep = with_input(ps[0].window, env, j, |inp, cl| {
            let mut sinks: Vec<RunSink> = g.iter().map(|_| env.sink(inp, from_min)).collect();
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
                    tally(sink, c, from_min, keep, mark, env.extra(inp))
                })
                .collect::<Vec<JobOut>>()
        });
        match prep {
            Prep::Ready(v) => {
                for (&i, o) in g.iter().zip(v) {
                    out[i] = Some(Outcome::Done(Box::new(o)));
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
        rearm: mode.rearm,
        // D5(ii): `from_min` у каждого задания свой, подставляется в `gated` на задании
        entry_from: None,
    }
}

const CELLS_HEADER: &str = "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh";

fn read_cells(path: &Path, kind: Kind, mode: Mode) -> Result<Vec<Cell>> {
    let txt = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    check_header(path, &txt, CELLS_HEADER)?;
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
        let window: usize = f[1].parse().with_context(at)?;
        if window == 0 {
            bail!("{}: window должно быть ≥ 1", at());
        }
        let p = cell_params(
            kind,
            mode,
            window,
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
    if window == 0 {
        bail!("--window должно быть ≥ 1");
    }
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
    opens: Vec<(usize, OpenSnap, Mtm)>,
    ext: ExtStats,
    /// (задание, сутки, суммы за сутки закрытия, active) — `--job-day-out`.
    job_days: Vec<(usize, i64, [f64; 5], bool)>,
    /// Счётчики по заданиям — `--job-ext-out`.
    rows: Vec<JobRow>,
}

/// Счётчики одного задания клетки (`--job-ext-out`): статус, счётчики ядра, `pre_window`, оценка открытой позиции.
struct JobRow {
    job: usize,
    status: &'static str,
    c: Counters,
    pre: u64,
    pre_net: f64,
    open_pre: u64,
    mtm: Option<Mtm>,
}

impl Agg {
    /// `rows` — копить строки `--job-ext-out` (по строке на задание: память растёт с числом заданий, поэтому только по флагу).
    fn add(&mut self, job: usize, o: Outcome, rows: bool) {
        if rows {
            let (status, c, pre, pre_net, open_pre, mtm) = match &o {
                Outcome::Missing => ("missing", Counters::default(), 0, 0.0, 0, None),
                Outcome::NoSpan => ("no_span", Counters::default(), 0, 0.0, 0, None),
                Outcome::Done(d) => ("done", d.c, d.pre_window, d.pre_net_bps, d.open_pre, d.mtm),
            };
            self.rows.push(JobRow {
                job,
                status,
                c,
                pre,
                pre_net,
                open_pre,
                mtm,
            });
        }
        match o {
            Outcome::Missing => self.missing += 1,
            Outcome::NoSpan => self.no_span += 1,
            Outcome::Done(o) => {
                let o = *o;
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
                self.c.eval_minutes += o.c.eval_minutes;
                self.c.z_def_minutes += o.c.z_def_minutes;
                self.c.pos_z_nan_minutes += o.c.pos_z_nan_minutes;
                self.c.noentry_window_minutes += o.c.noentry_window_minutes;
                self.c.rearm_blocked += o.c.rearm_blocked;
                self.c.warmup_blocked += o.c.warmup_blocked;
                self.ext.merge(o.ext);
                self.job_days
                    .extend(o.jd.into_iter().map(|(d, v, a)| (job, d, v, a)));
                if let Some(m) = o.mtm {
                    self.mtm_n += 1;
                    let parts = [m.net_bps, m.gross_bps, m.fee_bps, m.funding_bps];
                    for (acc, x) in self.mtm.iter_mut().zip(parts) {
                        *acc += x;
                    }
                    self.mtm_stale_max = self.mtm_stale_max.max(m.stale_min);
                }
                if let Some((snap, m)) = o.open {
                    self.opens.push((job, snap, m));
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
    /// `--job-ext-out`: копить строки по заданиям.
    rows: bool,
}

impl Merge {
    fn put(&mut self, job: usize, outs: Vec<Outcome>) {
        self.pending.insert(job, outs);
        while let Some(outs) = self.pending.remove(&self.next) {
            for (agg, o) in self.aggs.iter_mut().zip(outs) {
                agg.add(self.next, o, self.rows);
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

/// Сделки выбранных заданий. Столбец `fill_x` — только при `--delay-x ≥ 1` (иначе он равен `fill_in`, заголовок прежний);
/// `exit_reason,beta_in` в конце — только при `--trades-ext`.
fn write_trades(
    path: &Path,
    cells: &[Cell],
    aggs: &[Agg],
    jobs: &[Job],
    fill_x: bool,
    ext: bool,
) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(
        w,
        "cell,job,y,x,dir,sig_in,fill_in,{}sig_out,fill_out,gross_bps,fee_bps,funding_bps,net_bps{}",
        if fill_x { "fill_x," } else { "" },
        if ext { ",exit_reason,beta_in" } else { "" }
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
            write!(
                w,
                "{},{},{},{},{},{}",
                t.sig_out, t.fill_out, t.gross_bps, t.fee_bps, t.funding_bps, t.net_bps
            )?;
            if ext {
                write!(w, ",{},{}", t.reason.as_str(), t.beta)?;
            }
            writeln!(w)?;
        }
    }
    w.flush()?;
    Ok(())
}

/// Сутки заданий (`--job-day-out`): `cell,job,y,x,day,net_bps,gross_bps,fee_bps,funding_bps,trades,active`; суммы за сутки
/// закрытия сделки, `active` 1/0. Строки клетки идут по номеру задания, затем по суткам.
fn write_job_day(path: &Path, cells: &[Cell], aggs: &[Agg], jobs: &[Job]) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(
        w,
        "cell,job,y,x,day,net_bps,gross_bps,fee_bps,funding_bps,trades,active"
    )?;
    for (cell, agg) in cells.iter().zip(aggs) {
        for (job, d, v, active) in &agg.job_days {
            let Some(j) = jobs.get(*job) else { continue };
            // число сделок — целое в f64, `{}` печатает его без дробной части
            let [net, gross, fee, funding, trades] = *v;
            writeln!(
                w,
                "{},{},{},{},{},{},{},{},{},{},{}",
                cell.label,
                job,
                j.y,
                j.x.join("+"),
                d,
                net,
                gross,
                fee,
                funding,
                trades,
                u8::from(*active)
            )?;
        }
    }
    w.flush()?;
    Ok(())
}

const EXT_OUT_COLUMNS: [&str; 30] = [
    "cell",
    "jobs_done",
    "eval_minutes",
    "z_def_minutes",
    "z_def_share",
    "minutes_in_pos",
    "pos_z_nan_minutes",
    "pos_z_nan_share",
    "noentry_window_minutes",
    "rearm_blocked",
    "trades_n",
    "exit_rule",
    "exit_stop",
    "exit_timeout",
    "open_end",
    "net_y_sum_bps",
    "net_gross_sum_bps",
    "net_y_mean_bps",
    "net_gross_mean_bps",
    "notional_mean",
    "beta_neg_share",
    "beta_min",
    "beta_p01",
    "beta_p05",
    "beta_p25",
    "beta_p50",
    "beta_p75",
    "beta_p95",
    "beta_p99",
    "beta_max",
];

/// Доля `a / b`; `nan`, если знаменателя нет.
fn share(a: u64, b: u64) -> String {
    if b == 0 {
        "nan".to_string()
    } else {
        (a as f64 / b as f64).to_string()
    }
}

/// Наибольший ранг: q-й процентиль (`q` — целое, 1…99) отсортированного ряда `v`; пусто — `nan`.
fn rank_pctl(v: &[f64], q: usize) -> String {
    if v.is_empty() {
        return "nan".to_string();
    }
    let i = (q * v.len()).div_ceil(100).max(1) - 1;
    v.get(i).map_or_else(|| "nan".to_string(), f64::to_string)
}

/// Расширенная сводка по клетке (`--ext-out`). Счётчики F-01 — по всему спану заданий после разогрева окна
/// (`eval_minutes`: минуты с `t + 1 ≥ window`), `minutes_in_pos` и `pos_z_nan_minutes` — по всему спану; разбивка выходов
/// и F-03 — по сделкам с входом не раньше `from_day`, как в суточном csv.
fn write_ext(path: &Path, cells: &[Cell], aggs: &mut [Agg]) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(w, "{}", EXT_OUT_COLUMNS.join(","))?;
    for (cell, g) in cells.iter().zip(aggs.iter_mut()) {
        // сортировка на месте: вектор β на сделку большой, копия при сортировке удваивала пик памяти (аудит L1-09)
        g.ext.betas.sort_by(f64::total_cmp);
        let (c, e) = (&g.c, &g.ext);
        let betas = &e.betas;
        let mean = |x: f64| {
            if e.n == 0 {
                "nan".to_string()
            } else {
                (x / e.n as f64).to_string()
            }
        };
        let mut f: Vec<String> = vec![cell.label.clone(), g.done.to_string()];
        f.push(c.eval_minutes.to_string());
        f.push(c.z_def_minutes.to_string());
        f.push(share(c.z_def_minutes, c.eval_minutes));
        f.push(c.minutes_in_pos.to_string());
        f.push(c.pos_z_nan_minutes.to_string());
        f.push(share(c.pos_z_nan_minutes, c.minutes_in_pos));
        f.push(c.noentry_window_minutes.to_string());
        f.push(c.rearm_blocked.to_string());
        f.push(e.n.to_string());
        f.push(e.n_rule.to_string());
        f.push(e.n_stop.to_string());
        f.push(e.n_timeout.to_string());
        f.push(c.open_at_end.saturating_sub(g.open_pre).to_string());
        f.push(e.net_y.to_string());
        f.push(e.net_gross.to_string());
        f.push(mean(e.net_y));
        f.push(mean(e.net_gross));
        f.push(mean(e.notional));
        f.push(share(e.beta_neg, e.n));
        f.push(
            betas
                .first()
                .map_or_else(|| "nan".to_string(), f64::to_string),
        );
        for q in [1, 5, 25, 50, 75, 95, 99] {
            f.push(rank_pctl(betas, q));
        }
        f.push(
            betas
                .last()
                .map_or_else(|| "nan".to_string(), f64::to_string),
        );
        writeln!(w, "{}", f.join(","))?;
    }
    w.flush()?;
    Ok(())
}

/// Открытые на конце окна позиции с оценкой (`--open-out`); `fill_x` пусто — нога X ещё не вошла (`--delay-x`).
fn write_open(path: &Path, cells: &[Cell], aggs: &[Agg], jobs: &[Job]) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(
        w,
        "cell,job,y,x,dir,sig_in,fill_in,fill_x,gross_bps,fee_bps,funding_bps,net_bps,stale_min"
    )?;
    for (cell, agg) in cells.iter().zip(aggs) {
        for (job, o, m) in &agg.opens {
            let Some(j) = jobs.get(*job) else { continue };
            writeln!(
                w,
                "{},{},{},{},{},{},{},{},{},{},{},{},{}",
                cell.label,
                job,
                j.y,
                j.x.join("+"),
                o.dir,
                o.sig_in,
                o.fill_in,
                o.fill_x.map_or_else(String::new, |v| v.to_string()),
                m.gross_bps,
                m.fee_bps,
                m.funding_bps,
                m.net_bps,
                m.stale_min
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
/// С `--no-entry-before-from` (`gate`) в конец дописывается `warmup_blocked`; без флага заголовок и строки прежние.
fn write_cells_out(
    path: &Path,
    cells: &[Cell],
    aggs: &[Agg],
    njobs: usize,
    mtm_on: bool,
    gate: bool,
) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    write!(w, "{}", CELLS_OUT_COLUMNS.join(","))?;
    if gate {
        write!(w, ",warmup_blocked")?;
    }
    writeln!(w)?;
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
        if gate {
            f.push(c.warmup_blocked.to_string());
        }
        writeln!(w, "{}", f.join(","))?;
    }
    w.flush()?;
    Ok(())
}

const JOB_EXT_COLUMNS: [&str; 18] = [
    "cell",
    "job",
    "y",
    "x",
    "status",
    "eval_minutes",
    "z_def_minutes",
    "minutes_in_pos",
    "pos_z_nan_minutes",
    "noentry_window_minutes",
    "entries",
    "trades",
    "pre_window",
    "pre_window_net_bps",
    "open_at_end",
    "open_pre_window",
    "warmup_blocked",
    "mtm_n",
];

/// MTM-столбцы `--job-ext-out`: сумма по позиции задания (0 или 1 позиция); `nan` без `--mtm-open`.
const JOB_EXT_MTM: [&str; 5] = [
    "mtm_net_bps",
    "mtm_gross_bps",
    "mtm_fee_bps",
    "mtm_funding_bps",
    "mtm_stale_min",
];

/// Счётчики по заданиям (`--job-ext-out`): строка на (клетка, задание) в порядке клеток и номера задания. `trades` — сделки ядра
/// (в том числе отброшенные как `pre_window`, как в `--cells-out`); сделок в окне задания `trades − pre_window`.
fn write_job_ext(
    path: &Path,
    cells: &[Cell],
    aggs: &[Agg],
    jobs: &[Job],
    mtm_on: bool,
) -> Result<()> {
    let mut w = BufWriter::new(File::create(path)?);
    writeln!(w, "{},{}", JOB_EXT_COLUMNS.join(","), JOB_EXT_MTM.join(","))?;
    for (cell, g) in cells.iter().zip(aggs) {
        for r in &g.rows {
            let Some(j) = jobs.get(r.job) else { continue };
            let c = &r.c;
            write!(
                w,
                "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
                cell.label,
                r.job,
                j.y,
                j.x.join("+"),
                r.status,
                c.eval_minutes,
                c.z_def_minutes,
                c.minutes_in_pos,
                c.pos_z_nan_minutes,
                c.noentry_window_minutes,
                c.entries,
                c.trades,
                r.pre,
                r.pre_net,
                c.open_at_end,
                r.open_pre,
                c.warmup_blocked,
                u8::from(r.mtm.is_some())
            )?;
            match (mtm_on, r.mtm) {
                (false, _) => write!(w, ",nan,nan,nan,nan,nan")?,
                (true, None) => write!(w, ",0,0,0,0,0")?,
                (true, Some(m)) => write!(
                    w,
                    ",{},{},{},{},{}",
                    m.net_bps, m.gross_bps, m.fee_bps, m.funding_bps, m.stale_min
                )?,
            }
            writeln!(w)?;
        }
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
        ext: a.ext_out.is_some(),
        job_day: a.job_day_out.is_some(),
        gate: a.no_entry_before_from,
        ys: &ys,
        xs,
    };
    let load_s = t0.elapsed().as_secs_f64();
    let next = AtomicUsize::new(0);
    let merge = Mutex::new(Merge {
        next: 0,
        pending: BTreeMap::new(),
        aggs: cells.iter().map(|_| Agg::default()).collect(),
        rows: a.job_ext_out.is_some(),
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
    let mut aggs = merge.into_inner().unwrap().aggs;
    write_daily(&a.daily_out, &cells, &aggs)?;
    if let Some(f) = &a.trades_out {
        write_trades(f, &cells, &aggs, &jobs, mode.delay_x > 0, a.trades_ext)?;
    }
    if let Some(f) = &a.ext_out {
        write_ext(f, &cells, &mut aggs)?;
    }
    if let Some(f) = &a.job_day_out {
        write_job_day(f, &cells, &aggs, &jobs)?;
    }
    if let Some(f) = &a.cells_out {
        write_cells_out(
            f,
            &cells,
            &aggs,
            jobs.len(),
            a.mtm_open,
            a.no_entry_before_from,
        )?;
    }
    if let Some(f) = &a.job_ext_out {
        write_job_ext(f, &cells, &aggs, &jobs, a.mtm_open)?;
    }
    if let Some(f) = &a.open_out {
        write_open(f, &cells, &aggs, &jobs)?;
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
    if a.no_entry_before_from {
        let w: u64 = aggs.iter().map(|g| g.c.warmup_blocked).sum();
        println!("запрет входа до from_min: подавлено {w} мин сигналов входа (сумма по клеткам)");
    }
    if let Some(l) = funding_line(a, &ys, xs) {
        println!("{l}");
    }
    Ok(())
}
