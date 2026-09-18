//! `lob bounce-grid` — вся сетка форм отскока В-58 (48) по символам и суткам
//! **одним процессом** (B6 плана: S1, S2, S4; аудит 18.09: K1, K2, K4).
//!
//! Что было (`tools/b5_grid.py`): отдельный процесс `lob backtest --touches`
//! на каждую пару «форма × символ» — 48 × 100 процессов, каждый декодировал
//! запись дважды (события для `hftbacktest` и повторный реплей для касаний),
//! гонял трекер уровней и держал в памяти всю сессию (64 Б × ~17 млн событий
//! на 20 ч ≈ 1.1 ГБ). Замер `docs/findings/backtest-speed-2026-09-18.md`:
//! форма на ZEC за 20 ч — 30 с, из них трекер 14.6 с и второй декод 2.7 с
//! повторялись 48 раз ради одних и тех же касаний.
//!
//! Что здесь: касания считаются **один раз** на символ (`replay_symbol`, S1);
//! события `hftbacktest` строятся **посуточно** (S4: память — одни сутки, не
//! сессия), и по одному потоку событий гонятся **все 48 форм** потоками
//! (`std::thread::scope`, S2): события — `&[Event]` только на чтение, у каждой
//! формы свой `Backtest` над **тем же буфером** (`with_backtest_over`: крейт
//! читает срез напрямую, без копии на форму — с копиями восемь потоков × сутки
//! ZEC × 64 Б роняли процесс нехваткой памяти, 2026-09-18). Модель исполнения
//! та же, что у `lob backtest --touches` (`build_backtest`, `drive_bounce`):
//! менять её ради скорости запрещено.
//!
//! Fail-closed (K1): символ без маркера `verify-<SYMBOL>.status == ok` в
//! корне **пропускается** с строкой stderr и счётчиком в сводке; снять
//! требование можно только явным `--allow-unverified` (отладочные данные,
//! такой прогон в `runs.csv` не идёт).
//!
//! Артефакты (`--out-dir`, обычно `data/b5/<метка>/`):
//! - `rounds.csv` — каждый круг сделки с `symbol`, `day_utc`, `form` (K2:
//!   интервал по суткам и стратификация по инструменту считаются из этого);
//! - `forms.csv` — строка на (символ, сутки, форма): сигналы, круги, промахи,
//!   причины выхода, сумма `net_bps`;
//! - `manifest.txt` — аргументы прогона.
//!
//! Сетка — ровно 48 форм в порядке `STOP_MODES × DEADLINE_SECS ×
//! EARLY_EXIT_LABELS` (`bounce_verdict`), имена те же, что читает вердикт
//! (`parse_form`): другое имя — отказ, не параметр.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use clap::Args;
use hftbacktest::types::Event as HbtEvent;

use super::backtest::{
    bounce_plan, count_feed_events, deadline_ns_from_secs, early_exit_ns_from_secs,
    exit_reason_label, feed_events_into, open_replay_feed, pool_order_qty, read_tick_step,
    PlanShape, StopModeArg,
};
use super::bounce_verdict::{parse_form, DEADLINE_SECS, EARLY_EXIT_LABELS, STOP_MODES};
use super::profiles::read_verify_marker;
use super::{
    replay_symbol_touches_only, resolve_h3_mode_full, session_parts_for, H3Args,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::lob::backtest::{
    drive_bounce, drive_bounce_windowed, roundtrip_net_bps, with_backtest_over, BounceRun,
    BounceSignal, DriveConfig, SignalWindows,
};
use crate::lob::levels::{LevelsConfig, TouchRecord};

/// Одна форма сетки В-58: имя каталога/колонки и её параметры.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GridForm {
    pub label: &'static str,
    pub stop: StopModeArg,
    pub deadline_secs: i64,
    pub early_exit_secs: Option<i64>,
}

/// Все 48 форм В-58 в порядке `STOP_MODES × DEADLINE_SECS × EARLY_EXIT_LABELS`
/// — тот же порядок, что перебирал `tools/b5_grid.py`, имена — те, что
/// читает `bounce_verdict::parse_form`.
pub fn grid_forms() -> Vec<GridForm> {
    let mut out =
        Vec::with_capacity(STOP_MODES.len() * DEADLINE_SECS.len() * EARLY_EXIT_LABELS.len());
    for stop in STOP_MODES {
        for deadline in DEADLINE_SECS {
            for early in EARLY_EXIT_LABELS {
                let label: &'static str =
                    Box::leak(format!("{stop}-{deadline}-{early}").into_boxed_str());
                let (stop_name, deadline_secs, early_exit_secs) =
                    parse_form(label).expect("сетка В-58 собрана из своих же констант");
                let stop = match stop_name {
                    "before" => StopModeArg::Before,
                    "at" => StopModeArg::At,
                    _ => StopModeArg::Behind,
                };
                out.push(GridForm {
                    label,
                    stop,
                    deadline_secs: deadline_secs as i64,
                    early_exit_secs: early_exit_secs.map(|s| s as i64),
                });
            }
        }
    }
    out
}

#[derive(Debug, Args)]
pub struct BounceGridArgs {
    /// Корень записи (`<SYMBOL>-<день>[-pN].binlog`, `instruments.csv`,
    /// маркеры `verify-<SYMBOL>.status`).
    #[arg(long)]
    pub root: PathBuf,
    /// Символы (повторяемый флаг); пусто — весь пул `instruments.csv` корня.
    #[arg(long = "symbol")]
    pub symbols: Vec<String>,
    /// Сутки UTC `YYYY-MM-DD` (повторяемый флаг); пусто — все сутки записи.
    #[arg(long = "day")]
    pub days: Vec<String>,
    /// RTT исполнения, нс (В-37: `assumed` 20 мс, флаг обязателен).
    #[arg(long)]
    pub median_rtt_ns: i64,
    #[arg(long)]
    pub p95_rtt_ns: i64,
    /// Лот в e9 — либо он, либо `--order-qty-from-pool`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
    /// Лот — `order_size_22a` от полей пула и цены последнего касания.
    #[arg(long, default_value_t = false)]
    pub order_qty_from_pool: bool,
    #[arg(long, default_value_t = false)]
    pub post_only: bool,
    #[command(flatten)]
    pub h3: H3Args,
    #[arg(long)]
    pub h3_k: Option<f64>,
    #[arg(long)]
    pub warmup_ms: Option<i64>,
    #[arg(long)]
    pub repeat_window_ms: Option<i64>,
    /// Потоков на сетку (умолчание — число ядер).
    #[arg(long)]
    pub threads: Option<usize>,
    /// Драйвер: `setups` — биржа только от касания до конца круга (умолчание,
    /// решение владельца 2026-09-18); `full` — сплошной прогон суток на форму,
    /// эталон для гейта «побайтово те же круги».
    #[arg(long, value_enum, default_value_t = DriverArg::Setups)]
    pub driver: DriverArg,
    /// Каталог артефактов (`rounds.csv`, `forms.csv`, `manifest.txt`).
    #[arg(long)]
    pub out_dir: PathBuf,
    /// Снять требование маркера сверки (отладочные данные; в `runs.csv` не идёт).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
}

/// Как гонять форму над сутками: сплошным прогоном или по сетапам.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum DriverArg {
    Full,
    Setups,
}

impl DriverArg {
    pub fn label(self) -> &'static str {
        match self {
            DriverArg::Full => "full",
            DriverArg::Setups => "setups",
        }
    }
}

/// Итог прогона — то, что печатает `lob bounce-grid`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BounceGridSummary {
    pub forms: usize,
    pub symbols_done: usize,
    pub symbols_skipped_unverified: usize,
    pub symbols_without_touches: usize,
    pub symbol_days: usize,
    pub rounds: u64,
    pub rounds_path: PathBuf,
    pub forms_path: PathBuf,
}

/// Результат одной формы на одних сутках одного символа.
struct FormDayResult {
    form: usize,
    run: BounceRun,
}

const ROUNDS_HEADER: [&str; 12] = [
    "symbol",
    "day_utc",
    "form",
    "signal_index",
    "t0_ns",
    "dir",
    "entry_px",
    "exit_px",
    "qty",
    "net_bps",
    "reason",
    "exit_ns",
];

const FORMS_HEADER: [&str; 20] = [
    "symbol",
    "day_utc",
    "form",
    "n_signals",
    "n_submitted",
    "n_fills",
    "n_busy",
    "entry_rejected",
    "entry_crossed",
    "sum_net_bps",
    "n_stop",
    "n_take",
    "n_trail",
    "n_deadline",
    "n_early",
    "n_horizon",
    "incomplete",
    "stop_mode",
    "n_residual_flattened",
    "signals_by_hour",
];

fn pool_symbols(root: &Path) -> anyhow::Result<Vec<String>> {
    let path = root.join("instruments.csv");
    let mut r = super::pick::instruments_csv_reader(&path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let mut out = Vec::new();
    for rec in r.records() {
        let rec = rec.map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        if let Some(s) = rec.get(0) {
            if !s.is_empty() && !s.starts_with('#') {
                out.push(s.to_string());
            }
        }
    }
    anyhow::ensure!(!out.is_empty(), "{}: пул пуст", path.display());
    Ok(out)
}

fn signals_for(
    touches: &[TouchRecord],
    tick: f64,
    form: &GridForm,
    post_only: bool,
) -> anyhow::Result<Vec<BounceSignal>> {
    let deadline_ns = deadline_ns_from_secs(form.deadline_secs)?;
    let early_exit_ns = early_exit_ns_from_secs(form.early_exit_secs)?;
    let mut signals: Vec<BounceSignal> = touches
        .iter()
        .map(|t| {
            let (sigma, plan) = bounce_plan(
                t,
                tick,
                form.stop,
                PlanShape {
                    post_only,
                    trail_bps: 0.0,
                    trail_activate_bps: 0.0,
                    grid_legs: 1,
                    grid_step_ticks: 0,
                    deadline_ns,
                    early_exit_ns,
                },
            );
            BounceSignal {
                t0_ns: t.start_ms.saturating_mul(1_000_000),
                sigma,
                plan,
                profile: 0,
            }
        })
        .collect();
    signals.sort_by_key(|s| s.t0_ns);
    Ok(signals)
}

/// События суток крейта из всех частей дня — в `Vec` **точного** размера:
/// сначала части считаются (`count_feed_events`), потом декодируются в
/// буфер с готовой ёмкостью. Рост удвоением держал старый и новый буфер
/// вместе (пик до 3× итога) и ронял сетку на сервере по OOM на сутках в
/// ~20 млн событий (2026-09-18); второй декод дешевле памяти.
fn day_events(parts: &[PathBuf]) -> anyhow::Result<Vec<HbtEvent>> {
    let mut total = 0usize;
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        total += count_feed_events(&mut feed);
    }
    let mut events: Vec<HbtEvent> = Vec::with_capacity(total);
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        feed_events_into(&mut feed, &mut events);
    }
    debug_assert_eq!(events.len(), total);
    Ok(events)
}

/// Параметры прогона суток одной структурой (clippy держит предел семи аргументов).
#[derive(Debug, Clone, Copy)]
struct DayParams {
    tick: f64,
    lot: f64,
    rtt_ns: i64,
    order_qty: f64,
    threads: usize,
    driver: DriverArg,
    post_only: bool,
}

/// Готовые результаты форм уходят в `sink` **по порядку форм**: форма,
/// закончившая раньше соседей с меньшим индексом, ждёт в буфере (не больше
/// числа потоков), чтобы дамп не зависел от числа потоков.
struct FormOrder<'a> {
    pending: BTreeMap<usize, (BounceRun, Vec<BounceSignal>)>,
    next_form: usize,
    done: usize,
    sink: &'a mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
}

/// Все 48 форм над одними сутками: потоки берут формы по счётчику. `Setups`
/// — один проход книги на сутки (`SignalWindows`, общий для форм), дальше у
/// каждой формы движок только внутри кругов; `Full` — у каждой формы свой
/// `Backtest` над всеми событиями суток (эталон гейта).
///
/// Память (сервер, 2026-09-18): сигналы формы строятся **в потоке, когда
/// форма взята** (48 форм × 100 тыс. касаний × 128 Б заранее — 600 МБ), а
/// результат формы отдаётся `sink` сразу и до конца суток не копится
/// (`FormOrder`). Возвращает число форм, отданных в `sink`.
fn drive_day(
    events: &[HbtEvent],
    touches: &[TouchRecord],
    forms: &[GridForm],
    p: DayParams,
    sink: &mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
) -> anyhow::Result<usize> {
    let windows = match p.driver {
        DriverArg::Full => None,
        DriverArg::Setups => {
            let t0s: Vec<i64> = touches
                .iter()
                .map(|t| t.start_ms.saturating_mul(1_000_000))
                .collect();
            let started = Instant::now();
            let w = SignalWindows::build(events, &t0s, p.tick, p.lot);
            eprintln!(
                "bounce-grid:   окна: снимков {} · уровней всего {} (в среднем {:.0} на снимок) · {:.2}s",
                w.len(),
                w.levels_total(),
                w.levels_total() as f64 / w.len().max(1) as f64,
                started.elapsed().as_secs_f64()
            );
            Some(w)
        }
    };
    let next = AtomicUsize::new(0);
    let failure: Mutex<Option<anyhow::Error>> = Mutex::new(None);
    let order = Mutex::new(FormOrder {
        pending: BTreeMap::new(),
        next_form: 0,
        done: 0,
        sink,
    });
    std::thread::scope(|scope| {
        for _ in 0..p.threads.max(1) {
            scope.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                if i >= forms.len() {
                    break;
                }
                if failure.lock().map(|f| f.is_some()).unwrap_or(true) {
                    break;
                }
                let cfg = DriveConfig {
                    order_qty: p.order_qty,
                    first_order_id: 1,
                };
                let step =
                    signals_for(touches, p.tick, &forms[i], p.post_only).and_then(|signals| {
                        let driven = match &windows {
                            Some(w) => drive_bounce_windowed(events, w, &signals, &cfg, p.rtt_ns),
                            None => with_backtest_over(events, p.tick, p.lot, p.rtt_ns, |bt| {
                                drive_bounce(bt, 0, &signals, &cfg)
                            }),
                        };
                        driven
                            .map(|run| (run, signals))
                            .map_err(|e| anyhow::anyhow!("форма #{i}: {e}"))
                    });
                let flushed = match step {
                    Ok((run, signals)) => match order.lock() {
                        Ok(mut o) => {
                            o.pending.insert(i, (run, signals));
                            let mut res = Ok(());
                            loop {
                                let form = o.next_form;
                                let Some((run, signals)) = o.pending.remove(&form) else {
                                    break;
                                };
                                res = (o.sink)(FormDayResult { form, run }, &signals);
                                o.next_form += 1;
                                o.done += 1;
                                if res.is_err() {
                                    break;
                                }
                            }
                            res
                        }
                        Err(_) => Err(anyhow::anyhow!("результаты форм: мьютекс")),
                    },
                    Err(e) => Err(e),
                };
                if let Err(e) = flushed {
                    if let Ok(mut f) = failure.lock() {
                        if f.is_none() {
                            *f = Some(e);
                        }
                    }
                    break;
                }
            });
        }
    });
    if let Some(e) = failure.into_inner().ok().flatten() {
        return Err(e);
    }
    let o = order
        .into_inner()
        .map_err(|_| anyhow::anyhow!("результаты форм: мьютекс"))?;
    anyhow::ensure!(
        o.pending.is_empty(),
        "формы без записи в дамп: {}",
        o.pending.len()
    );
    Ok(o.done)
}

struct Outputs {
    rounds: csv::Writer<std::fs::File>,
    forms: csv::Writer<std::fs::File>,
    rounds_path: PathBuf,
    forms_path: PathBuf,
}

/// Сигналы формы по часам UTC суток, `h0:h1:…:h23` (В-60): вердикт по одним
/// суткам кластеризует интервал `net_fill` по часам, и промахи (у них в
/// `rounds.csv` нет времени) раскладываются по часам из этого столбца.
fn signals_by_hour(signals: &[BounceSignal]) -> String {
    let mut by_hour = [0u64; 24];
    for s in signals {
        let secs = s.t0_ns.div_euclid(1_000_000_000).rem_euclid(86_400);
        by_hour[(secs / 3_600) as usize] += 1;
    }
    by_hour
        .iter()
        .map(u64::to_string)
        .collect::<Vec<_>>()
        .join(":")
}

impl Outputs {
    fn create(out_dir: &Path, header: &str) -> anyhow::Result<Self> {
        std::fs::create_dir_all(out_dir)?;
        let rounds_path = out_dir.join("rounds.csv");
        let forms_path = out_dir.join("forms.csv");
        let mut rf = std::fs::File::create(&rounds_path)?;
        writeln!(rf, "{header}")?;
        let mut ff = std::fs::File::create(&forms_path)?;
        writeln!(ff, "{header}")?;
        let mut rounds = csv::WriterBuilder::new().has_headers(false).from_writer(rf);
        rounds.write_record(ROUNDS_HEADER)?;
        let mut forms = csv::WriterBuilder::new().has_headers(false).from_writer(ff);
        forms.write_record(FORMS_HEADER)?;
        Ok(Self {
            rounds,
            forms,
            rounds_path,
            forms_path,
        })
    }

    fn write_form(
        &mut self,
        symbol: &str,
        day: &str,
        form: GridForm,
        signals: &[BounceSignal],
        run: &BounceRun,
    ) -> anyhow::Result<u64> {
        anyhow::ensure!(
            run.fill_reason.len() == run.fills.len()
                && run.fill_signal.len() == run.fills.len()
                && run.fill_exit_ns.len() == run.fills.len(),
            "{symbol} {day} {}: кругов {}, причин {}, сигналов {} — дамп не пишется",
            form.label,
            run.fills.len(),
            run.fill_reason.len(),
            run.fill_signal.len()
        );
        let mut sum_net = 0.0_f64;
        for (i, fill) in run.fills.iter().enumerate() {
            let net = roundtrip_net_bps(fill);
            if let Some(v) = net {
                sum_net += v;
            }
            let sig = run.fill_signal[i];
            let t0 = signals.get(sig).map(|s| s.t0_ns).unwrap_or(0);
            self.rounds.write_record([
                symbol.to_string(),
                day.to_string(),
                form.label.to_string(),
                sig.to_string(),
                t0.to_string(),
                fill.dir.to_string(),
                format!("{:.10}", fill.entry_px),
                format!("{:.10}", fill.exit_px),
                format!("{:.10}", fill.qty),
                net.map(|v| format!("{v:.6}"))
                    .unwrap_or_else(|| "not_measured".to_string()),
                exit_reason_label(run.fill_reason[i]).to_string(),
                run.fill_exit_ns[i].to_string(),
            ])?;
        }
        self.forms.write_record([
            symbol.to_string(),
            day.to_string(),
            form.label.to_string(),
            signals.len().to_string(),
            run.submitted_signal.len().to_string(),
            run.fills.len().to_string(),
            run.busy_signal.len().to_string(),
            run.entry_rejected.to_string(),
            run.entry_crossed.to_string(),
            format!("{sum_net:.6}"),
            run.exits.stop.to_string(),
            run.exits.take.to_string(),
            run.exits.trail.to_string(),
            run.exits.deadline.to_string(),
            run.exits.early.to_string(),
            run.exits.horizon.to_string(),
            run.incomplete.to_string(),
            format!("{:?}", form.stop).to_lowercase(),
            run.residual_flattened.to_string(),
            signals_by_hour(signals),
        ])?;
        self.rounds.flush()?;
        self.forms.flush()?;
        Ok(run.fills.len() as u64)
    }
}

pub fn run_bounce_grid(args: &BounceGridArgs) -> anyhow::Result<BounceGridSummary> {
    anyhow::ensure!(
        args.root.is_dir(),
        "{}: корень записи не каталог",
        args.root.display()
    );
    match (args.order_qty_e9, args.order_qty_from_pool) {
        (Some(_), true) => anyhow::bail!(
            "--order-qty-e9 и --order-qty-from-pool взаимоисключающие: лот задаётся одним способом"
        ),
        (None, false) => anyhow::bail!(
            "нужен --order-qty-e9 или --order-qty-from-pool: изобретённого умолчания нет (§9 плана)"
        ),
        _ => {}
    }
    let threads = args
        .threads
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1)
        })
        .max(1);
    let forms = grid_forms();
    let symbols = if args.symbols.is_empty() {
        pool_symbols(&args.root)?
    } else {
        args.symbols.clone()
    };

    let header = format!(
        "# lob bounce-grid: root={} days={} forms={} RTT={}нс assumed(В-37) h3={:?} lot={} threads={} driver={} verified={}",
        args.root.display(),
        if args.days.is_empty() {
            "all".to_string()
        } else {
            args.days.join("+")
        },
        forms.len(),
        args.median_rtt_ns,
        args.h3.h3_mode,
        match (args.order_qty_e9, args.order_qty_from_pool) {
            (Some(v), _) => format!("e9:{v}"),
            _ => "pool(22a)".to_string(),
        },
        threads,
        args.driver.label(),
        if args.allow_unverified {
            "allow-unverified(debug)"
        } else {
            "marker-required"
        }
    );
    let mut out = Outputs::create(&args.out_dir, &header)?;
    {
        let mut m = std::fs::File::create(args.out_dir.join("manifest.txt"))?;
        writeln!(m, "{header}")?;
        writeln!(m, "symbols={}", symbols.join(","))?;
        writeln!(
            m,
            "forms={}",
            forms.iter().map(|f| f.label).collect::<Vec<_>>().join(",")
        )?;
    }

    let mut summary = BounceGridSummary {
        forms: forms.len(),
        rounds_path: out.rounds_path.clone(),
        forms_path: out.forms_path.clone(),
        ..Default::default()
    };

    for symbol in &symbols {
        let marker = args.root.join(format!("verify-{symbol}.status"));
        if !args.allow_unverified && !read_verify_marker(&marker) {
            eprintln!(
                "bounce-grid: {symbol} — маркер {} не `ok`, символ пропущен (fail-closed; --allow-unverified для отладки)",
                marker.display()
            );
            summary.symbols_skipped_unverified += 1;
            continue;
        }
        let started = Instant::now();
        let parts = session_parts_for(&args.root, symbol)?;
        anyhow::ensure!(
            !parts.is_empty(),
            "{symbol}: частей записи в {} нет",
            args.root.display()
        );
        let (tick_e9, step_e9) = read_tick_step(&parts[0].path)?;
        let tick = tick_e9 as f64 / 1e9;
        let lot = step_e9 as f64 / 1e9;
        // Порог плотности — любой из режимов В-61 (`--h3-mode notional|strength|both`)
        // или прежние floor/percentile; тик и шаг лота — из заголовка бинлога.
        let mode = resolve_h3_mode_full(&args.root, symbol, &args.h3, args.h3_k, tick_e9, step_e9)?;
        let cfg_levels = LevelsConfig {
            mode,
            warmup_ms: args.warmup_ms.unwrap_or(DEFAULT_WARMUP_MS),
            repeat_window_ms: args.repeat_window_ms.unwrap_or(DEFAULT_REPEAT_WINDOW_MS),
        };
        // S1: касания один раз на символ — общие для всех 48 форм.
        let replay = replay_symbol_touches_only(&args.root, symbol, cfg_levels)?;
        let touches_total: usize = replay.days.iter().map(|d| d.touches.len()).sum();
        if touches_total == 0 {
            eprintln!("bounce-grid: {symbol} — касаний нет, символ пропущен");
            summary.symbols_without_touches += 1;
            continue;
        }
        let order_qty_e9 = match (args.order_qty_e9, args.order_qty_from_pool) {
            (Some(v), _) => v,
            _ => {
                let last = replay
                    .days
                    .iter()
                    .rev()
                    .find_map(|d| d.touches.last())
                    .expect("касания есть — проверено выше");
                pool_order_qty(
                    &args.root.join("instruments.csv"),
                    symbol,
                    last.price_tick,
                    tick_e9,
                )?
            }
        };
        let order_qty = order_qty_e9 as f64 / 1e9;

        let mut parts_by_day: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
        for p in &parts {
            parts_by_day
                .entry(p.day_utc.clone())
                .or_default()
                .push(p.path.clone());
        }

        for day in &replay.days {
            // `--day`: гнать только выбранные сутки. Касания при этом считаются
            // по всей записи символа (возраст уровня и прогрев трекера не
            // зависят от границы суток), так что круги выбранного дня те же,
            // что и в прогоне без фильтра.
            if !args.days.is_empty() && !args.days.contains(&day.day) {
                continue;
            }
            if day.touches.is_empty() {
                continue;
            }
            let Some(day_parts) = parts_by_day.get(&day.day) else {
                anyhow::bail!("{symbol}: сутки {} есть в реплее, но частей нет", day.day);
            };
            let day_started = Instant::now();
            // S4: события одних суток, не всей сессии.
            let events = day_events(day_parts)?;
            if events.is_empty() {
                eprintln!(
                    "bounce-grid: {symbol} {} — событий нет, сутки пропущены",
                    day.day
                );
                continue;
            }
            // S2: все формы над одним потоком событий, потоками; результат
            // каждой формы — сразу в дамп.
            let mut rounds: u64 = 0;
            let day_label = day.day.clone();
            let forms_done = {
                let out = &mut out;
                let forms_ref = &forms;
                let mut sink = |r: FormDayResult, signals: &[BounceSignal]| -> anyhow::Result<()> {
                    let n =
                        out.write_form(symbol, &day_label, forms_ref[r.form], signals, &r.run)?;
                    rounds = rounds.saturating_add(n);
                    Ok(())
                };
                drive_day(
                    &events,
                    &day.touches,
                    &forms,
                    DayParams {
                        tick,
                        lot,
                        rtt_ns: args.median_rtt_ns,
                        order_qty,
                        threads,
                        driver: args.driver,
                        post_only: args.post_only,
                    },
                    &mut sink,
                )?
            };
            anyhow::ensure!(
                forms_done == forms.len(),
                "{symbol} {}: форм посчитано {}, ожидалось {}",
                day.day,
                forms_done,
                forms.len()
            );
            summary.rounds = summary.rounds.saturating_add(rounds);
            summary.symbol_days += 1;
            eprintln!(
                "bounce-grid: {symbol} {} touches={} events={} forms={} rounds={} {:.1}s",
                day.day,
                day.touches.len(),
                events.len(),
                forms.len(),
                rounds,
                day_started.elapsed().as_secs_f64()
            );
        }
        summary.symbols_done += 1;
        eprintln!(
            "bounce-grid: {symbol} готов — суток {}, касаний {}, {:.1}s",
            replay.days.iter().filter(|d| !d.touches.is_empty()).count(),
            touches_total,
            started.elapsed().as_secs_f64()
        );
    }
    Ok(summary)
}

#[cfg(test)]
mod tests;
