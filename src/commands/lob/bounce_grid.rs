//! `lob bounce-grid` — вся сетка форм отскока (В-58 → В-62) по символам и суткам
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
//! сессия), и по одному потоку событий гонятся **все формы** потоками
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
//!   интервал по суткам и стратификация по инструменту считаются из этого) и
//!   фактическим исполнением круга (F4, В-78): `fill_frac` (доля исполненного
//!   от заказанного), `entry_vwap` (средняя цена исполненного входа),
//!   `legs_filled`, `legs_rejected` (ног, которых биржа не поставила, В-72);
//! - `forms.csv` — строка на (символ, сутки, форма): сигналы, круги, промахи,
//!   причины выхода, сумма `net_bps`, счётчик ног, не поставленных биржей
//!   (`n_rejected_postonly`);
//! - `manifest.txt` — аргументы прогона.
//!
//! Вход сделки-отскока — **пост-онли по умолчанию** (В-72: тейкерский вход в
//! момент касания — не наша сделка; заявка, пересёкшая спред, биржей
//! отклоняется и считается не поставленной). `--no-post-only` возвращает
//! обычный лимит `GTC` — это режим гейта «те же круги»: прежние
//! `rounds.csv`/`forms.csv` сняты до F4.
//!
//! Сетка (В-62) — `--stop-sigma × --take-sigma × DEADLINE_SECS` в этом порядке:
//! стоп и тейк — множители `σ_H` (реализованная волатильность середины за
//! окно дедлайна, `lob::sigma`), полы — тик за плотностью и `--take-floor-fees`
//! круговых комиссий (`backtest::bounce_plan`). Множители — числа владельца,
//! умолчаний нет; имя формы `s<a>-t<b>-<H>` то же, что читает вердикт
//! (`bounce_verdict::form_label`/`parse_form`). Досрочный выход В-58 п. 5 в
//! сетку не входит (измерен сеткой в тиках; вернуть — отдельной
//! предрегистрацией). Касание без `σ` (окно упирается в начало записи)
//! сигнала у формы не даёт и считается в `n_skipped`. Ось стороны
//! (`--side bid|ask`, этап 1 дороги к альфе, `side-asymmetry-2026-09-19.md`):
//! касания другой стороны выбывают до форм и считаются там же.
//!
//! Касания — из реплея книги (как было) или из кэша `--touches-from <dir>`:
//! CSV ночного H3 (`study/touches/<сутки>/touches-<SYMBOL>.csv`,
//! `nightly-grid.sh`). Реплей — 83 % времени сетки (замер 19.09: 29 монет ×
//! 3 суток — 1176 с из 1419), трекер уровней в обоих суточный, так что
//! касания те же побайтово (`touches::read_touches_csv`, тест обратимости;
//! гейт «те же `rounds.csv`/`forms.csv`» на монетах — `docs/COMMANDS.md`).
//! σ-формы (`s<a>`/`t<b>`) из кэша не считаются: `σ` в CSV — суточный ряд с
//! шестью знаками, а не ряд всей записи.
//!
//! Несколько семей флоров одним процессом — `--set <имя>:<фильтры>` (20.09):
//! события суток декодируются и окна `SignalWindows` строятся **один раз**,
//! дальше формы гонятся для каждого набора фильтров касаний (возраст, сила,
//! сторона, фронтран, съедание стены `eaten=`, ход до касания и режим
//! `ret1h_min=…`/`pool4h_max=…`/`btc4h_min=…` — S4 плана по сторонам, режим
//! из `--regime-from study/regime`) над теми же событиями; артефакты — `<out-dir>/<имя>/`
//! (тот же `rounds.csv`/`forms.csv`/`manifest.txt`, вердикт читает
//! подкаталог). Без `--set` — один набор из флагов командной строки в
//! `<out-dir>`, байт в байт как раньше; с `--set` флаги фильтров у команды
//! запрещены (набор — единственный источник). Ночь из девяти сеток базы
//! стоила девять декодов тех же суток на монету.
//!
//! Модель очереди и исполнения — `--queue-model risk-adverse|prob:<n>` (F3
//! этапа F, 20.09): **обязательный флаг, умолчания в коде нет**; `risk-adverse`
//! — прежний движок (числа прежних прогонов не меняются, гейт «те же круги»),
//! `prob:<n>` — модель очереди по объёму с частичным исполнением (`n` — число
//! предрегистрации, у крейта в примерах 3.0 — не наше умолчание). Три пути
//! исполнения крейта и их оптимизм — в doc-комментарии
//! `lob::backtest::build_backtest` и в шапке `forms.csv`/`manifest.txt`
//! (`queue=…`, `paths=…`); счётчик кругов по пути (3) — колонка
//! `n_fill_by_cross` в `forms.csv` (крейт пути не отдаёт: детектор
//! `lob::backtest` по буферу последних сделок, отсрочка вердикта на шаг —
//! локальная метка сделки отстаёт от биржевой).
//!
//! Форма входа и сигнал — F6 этапа F (В-73): `--signal touch|approach`
//! (умолчание `touch` — гейт «те же круги») выбирает, от чего строится план
//! (`bounce_plan`/`approach_plan`): от касания или от записи подхода F1
//! (`approaches-<SYMBOL>.csv` того же кэша `--touches-from`; `t0` — `arm_ms`,
//! снимок книги на взводе). `--entry-form` (повторяемый — ось сетки) —
//! `single@fr` (умолчание: прежняя одиночная нога у фронтранера; имя формы
//! остаётся трёхпольным) или `ladder<N>x<from>..<to>[w<k>]`: `N` ног целыми
//! тиками от `from` до `to` bps **над стеной** (для аска — под), равными
//! долями либо весом нижней ноги `k`. Числа — из имени формы
//! (предрегистрация), умолчаний нет; нога, пересёкшая лучший аск, биржей не
//! ставится (пост-онли `GTX`, `n_rejected_postonly`, F4). Имя формы —
//! `<вход>-<стоп>-<тейк>-<H>` с хвостовыми `-ttl<режим>` (F5) и `-<выход>`
//! (F7), читает `bounce_verdict::parse_form_fields`.

mod args;
mod cache;
mod carry;
mod drive;
mod entry_sigma;
mod forms;
mod outputs;
mod plan;
mod sets;
mod verdict;

use args::SignalArg;
pub use args::{BounceGridArgs, BounceGridSummary};
pub(crate) use cache::cached_touches;
use cache::{cached_approaches, DayTouches};
use carry::{carry_window_ns, extend_with_carry};
use drive::{day_events, day_windows, drive_day, DayParams, DayRows, OrderSizing};
use entry_sigma::EntrySigma;
pub use forms::{grid_forms, ExitForm, GridForm};
use outputs::FormDayResult;
pub(crate) use plan::{ensure_holds_at_touch_checkable, pool_symbols};
use plan::{open_outputs, plan_grid, GridPlan};
use sets::SetPaths;
pub(crate) use sets::{read_regime_day, touch_contexts, FilterSet, RegimeDay, TouchFilter};

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use super::backtest::{read_day_schedule, read_tick_step};
use super::profiles::read_verify_marker;
use super::{
    replay_symbol_touches_and_second_mids, resolve_h3_mode_full, session_parts_for,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::book::Side;
use crate::lob::backtest::{BounceSignal, CompactEvent, RoundMemo, SignalWindows};
use crate::lob::levels::LevelsConfig;
use crate::lob::sigma::SigmaSeries;

type CarryKey = (String, Option<PathBuf>, Option<i64>);
type SharedHit = (Arc<Vec<CompactEvent>>, Option<i64>, bool);

/// TK-029 (`--extra-runs`): события суток символа (после довеска), прочитанные один раз на все прогоны с
/// тем же ключом (сутки, `--carry-root`, окно переноса). Держит только текущий символ.
#[derive(Default)]
struct SharedEvents {
    symbol: String,
    map: BTreeMap<CarryKey, SharedHit>,
    /// Окна сетапов суток (В-183, TK-048): чистая функция событий и `t0` ⇒ один проход на символ-сутки для
    /// всех прогонов; запись — отсортированные `t0` и окна (+ тик/лот: другой тик — другие окна).
    windows: BTreeMap<CarryKey, SharedWindows>,
    /// `t0` всех прогонов символа по суткам, заранее из кэшей касаний/подходов: окна строятся один раз на все.
    pre_t0: BTreeMap<String, Vec<i64>>,
}

type SharedWindows = (Vec<i64>, u64, u64, Arc<SignalWindows>);

impl SharedEvents {
    fn get(&self, symbol: &str, key: &CarryKey) -> Option<SharedHit> {
        if self.symbol == symbol {
            self.map.get(key).cloned()
        } else {
            None
        }
    }

    fn set_pre(&mut self, symbol: &str, pre: BTreeMap<String, Vec<i64>>) {
        self.symbol = symbol.to_string();
        self.map.clear();
        self.windows.clear();
        self.pre_t0 = pre;
    }

    fn put(&mut self, symbol: &str, key: CarryKey, hit: SharedHit) {
        if self.symbol != symbol {
            self.symbol = symbol.to_string();
            self.map.clear();
            self.windows.clear();
            self.pre_t0.clear();
        }
        self.map.insert(key, hit);
    }
}

#[derive(clap::Parser)]
struct ExtraRun {
    #[command(flatten)]
    args: BounceGridArgs,
}

/// Один прогон сетки: разобранный план, открытые выходы и итог; символы идут по одному (`run_symbol`).
struct GridRun<'a> {
    args: &'a BounceGridArgs,
    queue_model: crate::lob::backtest::QueueModelKind,
    threads: usize,
    band_exit_bps: f64,
    forms: Vec<GridForm>,
    sets: Vec<FilterSet>,
    set_forms: Option<Vec<Vec<usize>>>,
    need_regime: bool,
    need_ret: bool,
    symbols: Vec<String>,
    outs: Vec<outputs::Outputs>,
    regime_days: BTreeMap<String, RegimeDay>,
    carry_window: Option<i64>,
    summary: BounceGridSummary,
}

pub fn run_bounce_grid(args: &BounceGridArgs) -> anyhow::Result<BounceGridSummary> {
    if std::env::var_os("ALPHA_ATTEMPT_STATS").is_some() {
        crate::lob::backtest::fast_depth::BAND_STATS_ON
            .store(true, std::sync::atomic::Ordering::Relaxed);
    }
    let Some(path) = args.extra_runs.as_deref() else {
        let mut run = GridRun::new(args)?;
        for symbol in run.symbols.clone() {
            run.run_symbol(&symbol, None)?;
        }
        return Ok(run.summary);
    };
    let text = std::fs::read_to_string(path)
        .map_err(|e| anyhow::anyhow!("--extra-runs {}: {e}", path.display()))?;
    let mut extras: Vec<BounceGridArgs> = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parsed = <ExtraRun as clap::Parser>::try_parse_from(
            std::iter::once("bounce-grid").chain(line.split_whitespace()),
        )
        .map_err(|e| anyhow::anyhow!("--extra-runs {} строка {}: {e}", path.display(), i + 1))?;
        anyhow::ensure!(
            parsed.args.extra_runs.is_none(),
            "--extra-runs {} строка {}: вложенный --extra-runs",
            path.display(),
            i + 1
        );
        extras.push(parsed.args);
    }
    let mut runs = vec![GridRun::new(args)?];
    for e in &extras {
        runs.push(GridRun::new(e)?);
    }
    let mut order: Vec<String> = runs[0].symbols.clone();
    for r in &runs[1..] {
        for s in &r.symbols {
            if !order.contains(s) {
                order.push(s.clone());
            }
        }
    }
    let mut shared = SharedEvents::default();
    for symbol in &order {
        let mut pre: BTreeMap<String, Vec<i64>> = BTreeMap::new();
        if !args.windows_check {
            for r in runs.iter().filter(|r| r.symbols.contains(symbol)) {
                for (day, t0s) in r.prescan_t0(symbol) {
                    pre.entry(day).or_default().extend(t0s);
                }
            }
        }
        shared.set_pre(symbol, pre);
        for r in runs.iter_mut() {
            if r.symbols.contains(symbol) {
                r.run_symbol(symbol, Some(&mut shared))?;
            }
        }
    }
    for (i, r) in runs.iter().enumerate().skip(1) {
        let s = &r.summary;
        eprintln!(
            "bounce-grid[доп. прогон {i}]: форм {} · символов {} (без маркера {}, без касаний {}) · символ-суток {} · кругов {} · {} · {}",
            s.forms,
            s.symbols_done,
            s.symbols_skipped_unverified,
            s.symbols_without_touches,
            s.symbol_days,
            s.rounds,
            s.rounds_path.display(),
            s.forms_path.display()
        );
    }
    Ok(std::mem::take(&mut runs[0].summary))
}

impl<'a> GridRun<'a> {
    /// `t0` сетапов прогона по суткам из кэша касаний/подходов (дёшево, без реплея); нет кэша — пусто:
    /// тогда окна строятся прежним объединением по ходу прогонов.
    fn prescan_t0(&self, symbol: &str) -> BTreeMap<String, Vec<i64>> {
        let args = self.args;
        let mut out = BTreeMap::new();
        let Some(dir) = args.touches_from.as_deref() else {
            return out;
        };
        let Ok(parts) = session_parts_for(&args.root, symbol) else {
            return out;
        };
        let days: std::collections::BTreeSet<String> =
            parts.iter().map(|p| p.day_utc.clone()).collect();
        let got = if args.signal == SignalArg::Approach {
            cached_approaches(dir, symbol, days.iter())
        } else {
            cached_touches(dir, symbol, days.iter(), false)
        };
        for d in got.unwrap_or_default() {
            let t0s = d
                .touches
                .iter()
                .map(|t| t.start_ms.saturating_mul(1_000_000))
                .collect();
            out.insert(d.day, t0s);
        }
        out
    }

    fn new(args: &'a BounceGridArgs) -> anyhow::Result<Self> {
        let plan = plan_grid(args)?;
        let outs = open_outputs(args, &plan)?;
        // Перенос круга через полночь (`--carry-root`): окно — один раз на прогон,
        // те же числа сетки для всех символов и суток (`carry_window_ns`).
        let carry_window = match &args.carry_root {
            Some(_) => Some(carry_window_ns(
                &plan.entry_ttls,
                &plan.deadlines,
                args.p95_rtt_ns.taker_ns,
            )?),
            None => None,
        };
        let summary = BounceGridSummary {
            forms: plan.forms.len(),
            rounds_path: outs[0].rounds_path.clone(),
            forms_path: outs[0].forms_path.clone(),
            sets: plan
                .sets
                .iter()
                .zip(&outs)
                .map(|(s, o)| SetPaths {
                    name: s.name.clone(),
                    rounds_path: o.rounds_path.clone(),
                    forms_path: o.forms_path.clone(),
                })
                .collect(),
            ..Default::default()
        };
        let GridPlan {
            queue_model,
            threads,
            band_exit_bps,
            forms,
            sets,
            set_forms,
            need_regime,
            need_ret,
            symbols,
            ..
        } = plan;
        Ok(Self {
            args,
            queue_model,
            threads,
            band_exit_bps,
            forms,
            sets,
            set_forms,
            need_regime,
            need_ret,
            symbols,
            outs,
            // Режим суток читается один раз на сутки (общий для символов).
            regime_days: BTreeMap::new(),
            carry_window,
            summary,
        })
    }

    fn run_symbol(
        &mut self,
        symbol: &str,
        mut shared: Option<&mut SharedEvents>,
    ) -> anyhow::Result<()> {
        let args = self.args;
        let queue_model = self.queue_model;
        let threads = self.threads;
        let band_exit_bps = self.band_exit_bps;
        let need_regime = self.need_regime;
        let need_ret = self.need_ret;
        let carry_window = self.carry_window;
        let forms = &self.forms;
        let sets = &self.sets;
        let outs = &mut self.outs;
        let regime_days = &mut self.regime_days;
        let summary = &mut self.summary;
        // `--cells` (T-38): у каждого набора — свои формы (номера в `forms`); без флага — все формы.
        let all_forms: Vec<usize> = (0..forms.len()).collect();
        let set_form_ids: Vec<&[usize]> = match &self.set_forms {
            Some(v) => v.iter().map(Vec::as_slice).collect(),
            None => vec![all_forms.as_slice(); sets.len()],
        };

        let marker = args.root.join(format!("verify-{symbol}.status"));
        if args.verdict_csv.is_none() && !args.allow_unverified && !read_verify_marker(&marker) {
            eprintln!(
                "bounce-grid: {symbol} — маркер {} не `ok`, символ пропущен (fail-closed; --allow-unverified для отладки)",
                marker.display()
            );
            summary.symbols_skipped_unverified += 1;
            return Ok(());
        }
        let started = Instant::now();
        let parts = session_parts_for(&args.root, symbol)?;
        anyhow::ensure!(
            !parts.is_empty(),
            "{symbol}: частей записи в {} нет",
            args.root.display()
        );
        let (tick_e9, step_e9) = read_tick_step(&parts[0].path)?;
        // В-172: сетка символа одна на запись (реплей уровней тоже берёт parts[0]); часть с другой сеткой — отказ, не тихий счёт.
        for p in &parts[1..] {
            let g = read_tick_step(&p.path)?;
            anyhow::ensure!(
                g == (tick_e9, step_e9),
                "{symbol}: сетка (тик, лот) {} = {g:?}, а первой части записи {} = ({tick_e9}, {step_e9}) — смешанные сетки в одной записи не поддержаны (сутки со сменой шага v4 считать отдельным корнем)",
                p.path.display(),
                parts[0].path.display()
            );
        }
        let tick = tick_e9 as f64 / 1e9;
        let lot = step_e9 as f64 / 1e9;
        // Порог плотности — любой из режимов В-61 (`--h3-mode notional|strength|both`)
        // или прежние floor/percentile; тик и шаг лота — из заголовка бинлога.
        let mode = resolve_h3_mode_full(&args.root, symbol, &args.h3, args.h3_k, tick_e9, step_e9)?;
        ensure_holds_at_touch_checkable(&mode, symbol)?;
        let cfg_levels = LevelsConfig {
            mode,
            warmup_ms: args.warmup_ms.unwrap_or(DEFAULT_WARMUP_MS),
            repeat_window_ms: args.repeat_window_ms.unwrap_or(DEFAULT_REPEAT_WINDOW_MS),
            approach_bps: None,
            approach_min_age_ms: 0,
        };
        let mut parts_by_day: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
        for p in &parts {
            parts_by_day
                .entry(p.day_utc.clone())
                .or_default()
                .push(p.path.clone());
        }
        // K1 по суткам (`--verdict-csv`, В-181): вместо маркера символа — вердикт гейта на каждые сутки;
        // сутки не «пускаем» или без строки вердикта отброшены с причиной (fail-closed).
        if let Some(vp) = args.verdict_csv.as_deref() {
            let verdicts = verdict::read_day_verdicts(vp, symbol)?;
            let before = parts_by_day.len();
            parts_by_day.retain(|day, _| {
                let allowed = match verdicts.get(day) {
                    Some((true, _)) => true,
                    Some((false, why)) => {
                        eprintln!("bounce-grid: {symbol} {day} — отказ вердикта: {why}");
                        false
                    }
                    None => {
                        eprintln!(
                            "bounce-grid: {symbol} {day} — отказ: суток нет в вердикте {}",
                            vp.display()
                        );
                        false
                    }
                };
                allowed
            });
            summary.days_refused_verdict += before - parts_by_day.len();
            if parts_by_day.is_empty() {
                eprintln!(
                    "bounce-grid: {symbol} — ни одних суток с вердиктом «пускаем», символ пропущен"
                );
                summary.symbols_skipped_unverified += 1;
                return Ok(());
            }
        }
        // Довесок (`--carry-root`): части того же символа во **всей** записи
        // (не студийном `--root` одного дня), тем же резолвером, что и выше
        // (`session_parts_for`) — понадобятся только на границе D/D+1 (день
        // без своих частей в этом корне — перенос выключен для символа, не
        // отказ всей сетки: корень может знать не про все символы `--root`).
        let carry_parts_by_day: BTreeMap<String, Vec<PathBuf>> = match &args.carry_root {
            Some(dir) => match session_parts_for(dir, symbol) {
                Ok(parts) => {
                    let mut m: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
                    for p in parts {
                        m.entry(p.day_utc.clone()).or_default().push(p.path.clone());
                    }
                    m
                }
                Err(e) => {
                    eprintln!(
                        "bounce-grid: {symbol} — --carry-root {}: {e}, перенос выключен для символа",
                        dir.display()
                    );
                    BTreeMap::new()
                }
            },
            None => BTreeMap::new(),
        };
        // S1: касания один раз на символ — общие для всех форм; из кэша
        // `--touches-from` (сутки корня) или реплеем книги, тогда вместе с
        // ними срезы середины по границам секунд — для ряда `σ` (В-62).
        // F6 (В-73): `--signal approach` — записи подхода из того же кэша
        // (`approaches-<SYMBOL>.csv`, F1); реплея подходов нет — полосу `D`
        // задаёт прогон `lob touches --approach-bps` (проверено выше).
        if args.touches_cache_only && args.signal != SignalArg::Approach {
            if let Some(dir) = args.touches_from.as_deref() {
                if !dir.join(format!("touches-{symbol}.csv")).is_file() {
                    let before = parts_by_day.len();
                    parts_by_day.retain(|day, _| {
                        dir.join(day)
                            .join(format!("touches-{symbol}.csv"))
                            .is_file()
                    });
                    if parts_by_day.is_empty() {
                        eprintln!(
                            "bounce-grid: {symbol} — в кэше касаний нет ни одних суток корня, символ пропущен (--touches-cache-only)"
                        );
                        summary.symbols_without_touches += 1;
                        return Ok(());
                    }
                    if parts_by_day.len() < before {
                        eprintln!(
                            "bounce-grid: {symbol} — суток без кэша касаний {} из {before}, пропущены (--touches-cache-only)",
                            before - parts_by_day.len()
                        );
                    }
                }
            }
        }
        let (days, sigma_series) = if args.signal == SignalArg::Approach {
            let dir = args
                .touches_from
                .as_deref()
                .expect("--signal approach без --touches-from отвергнут выше");
            // Суток в кэше нет — символ пропускается, а не роняет весь прогон
            // (как у `lob fill-capacity --targets approaches`, F2): реплея
            // подходов у сетки нет, полосу `D` знает только прогон F1.
            let Ok(days) = cached_approaches(dir, symbol, parts_by_day.keys()) else {
                eprintln!(
                    "bounce-grid: {symbol} — кэш подходов не годится, символ пропущен (нужен прогон `lob touches --approach-bps D`)"
                );
                summary.symbols_without_touches += 1;
                return Ok(());
            };
            // T-35: `frontrun_min=` на подходах читает `frontrun_lots_at_arm` (T-28); в старом кэше колонки нет
            // (читается как −1) — отказ, а не молчаливый ноль сигналов.
            anyhow::ensure!(
                !sets.iter().any(|s| s.frontrun_min_lots.is_some())
                    || days.iter().all(|d| d.touches.iter().all(|t| t.frontrun_lots >= 0)),
                "{symbol}: `frontrun_min=` на подходах — нужен кэш с колонкой frontrun_lots_at_arm (T-28, `{}`)",
                dir.display()
            );
            // Г-07: `stack_min=` на подходах читает `stack_levels_at_arm` (те же колонки T-28, что
            // `frontrun_lots_at_arm`); старый кэш без них — отказ, а не молчаливый ноль сигналов.
            anyhow::ensure!(
                !sets.iter().any(|s| s.stack_min.is_some())
                    || days.iter().all(|d| d.touches.iter().all(|t| t.frontrun_lots >= 0)),
                "{symbol}: `stack_min=` на подходах — нужен кэш с колонкой stack_levels_at_arm (T-28, `{}`)",
                dir.display()
            );
            // TK-012: `--p08-cols` читает колонки `ArmP08` кэша подходов; старый кэш без них — отказ,
            // а не пустые клетки.
            anyhow::ensure!(
                !args.p08_cols
                    || days.iter().all(|d| {
                        d.approaches
                            .as_deref()
                            .is_some_and(|ap| ap.iter().all(|a| a.p08.is_some()))
                    }),
                "{symbol}: --p08-cols — нужен кэш подходов с колонками traded_lots_at_arm… (TK-012, `{}`), пересчитайте `lob touches --approach-bps`",
                dir.display()
            );
            summary.symbols_from_cache += 1;
            (days, SigmaSeries::from_mids(&[]))
        } else {
            match args
                .touches_from
                .as_deref()
                .map(|dir| cached_touches(dir, symbol, parts_by_day.keys(), need_ret))
            {
                Some(Ok(days)) => {
                    summary.symbols_from_cache += 1;
                    (days, SigmaSeries::from_mids(&[]))
                }
                Some(Err(why)) if args.touches_cache_only => {
                    eprintln!(
                        "bounce-grid: {symbol} — кэш касаний не годится ({why}), символ пропущен (--touches-cache-only)"
                    );
                    summary.symbols_without_touches += 1;
                    return Ok(());
                }
                other => {
                    if let Some(Err(why)) = other {
                        eprintln!("bounce-grid: {symbol} — кэш касаний не годится ({why}), реплей");
                    }
                    let replay = replay_symbol_touches_and_second_mids(
                        &args.root,
                        symbol,
                        cfg_levels,
                        args.carry_age,
                    )?;
                    let mut all =
                        Vec::with_capacity(replay.days.iter().map(|d| d.mids.len()).sum());
                    for d in &replay.days {
                        all.extend(d.mids.iter().copied());
                    }
                    let days = replay
                        .days
                        .into_iter()
                        .map(|d| DayTouches {
                            rets: d
                                .touches
                                .iter()
                                .map(|t| {
                                    std::array::from_fn(|k| {
                                        super::touches::pre_touch_return_bps_csv(
                                            &d.mids,
                                            t.start_ms,
                                            super::touches::PRE_TOUCH_MS[k],
                                        )
                                    })
                                })
                                .collect(),
                            day: d.day,
                            touches: d.touches,
                            approaches: None,
                        })
                        .collect();
                    (days, SigmaSeries::from_mids(&all))
                }
            }
        };
        let touches_total: usize = days.iter().map(|d| d.touches.len()).sum();
        if touches_total == 0 {
            let what = if args.signal == SignalArg::Approach {
                "записей подхода"
            } else {
                "касаний"
            };
            eprintln!("bounce-grid: {symbol} — {what} нет, символ пропущен");
            summary.symbols_without_touches += 1;
            return Ok(());
        }
        let sizing = OrderSizing::from_args(args, symbol)?;
        // В-131: таблица σ на взводе — одна на символ (все сутки записи).
        let entry_sigma = match args.sigma_from.as_deref() {
            Some(dir) => Some(EntrySigma::read(dir, symbol)?),
            None => None,
        };
        // Явный лот обязан быть целым числом шагов записи: ноги лестницы
        // (R3) считаются целыми шагами, и некратный лот молча менял бы размер
        // круга (0.25 при шаге 0.1 — `round(2.5)` = 3 шага, +20 %). Лот пула и
        // номинал (`--order-qty-from-pool`/`--order-usd`) кратны по построению.
        // Шаг записи — `qty_step` пула на момент записи (коллектор пишет его в
        // заголовок бинлога, `session/pool.rs`). Нуль — тоже отказ: круг без
        // размера (`drive_signal` проверяет это только в отладочной сборке).
        if let OrderSizing::Fixed(v) = sizing {
            anyhow::ensure!(
                v > 0 && step_e9 > 0 && v % step_e9 == 0,
                "{symbol}: --order-qty-e9 {v} не положителен или не кратен шагу лота записи                  {step_e9} (1e-9, qty_step пула при записи) — ноги лестницы округлили бы его молча"
            );
        }

        for day in &days {
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
            let retries_before =
                crate::lob::backtest::HORIZON_RETRIES.load(std::sync::atomic::Ordering::Relaxed);
            let skips_before =
                crate::lob::backtest::HOLD_SKIPS.load(std::sync::atomic::Ordering::Relaxed);
            // S4: события одних суток, не всей сессии. TK-029: при `--extra-runs` сутки символа
            // читаются один раз на все прогоны с тем же переносом (`SharedEvents`).
            let carry_key = (day.day.clone(), args.carry_root.clone(), carry_window);
            let (base, carry_boundary_ns, carry_unverified) =
                match shared.as_ref().and_then(|s| s.get(symbol, &carry_key)) {
                    Some(hit) => hit,
                    None => {
                        let mut ev = day_events(day_parts)?;
                        // Довесок (`--carry-root`): дописывает события D+1 в окне переноса
                        // ДО построения окон сетапов; сигналы дня от довеска не зависят.
                        let carry = match carry_window {
                            Some(window) if !ev.is_empty() => extend_with_carry(
                                &mut ev,
                                &day.day,
                                args.carry_root.as_deref(),
                                &carry_parts_by_day,
                                symbol,
                                window,
                            )?,
                            _ => (None, false),
                        };
                        let hit = (Arc::new(ev), carry.0, carry.1);
                        if let Some(s) = shared.as_mut() {
                            s.put(symbol, carry_key.clone(), hit.clone());
                        }
                        hit
                    }
                };
            if base.is_empty() {
                eprintln!(
                    "bounce-grid: {symbol} {} — событий нет, сутки пропущены",
                    day.day
                );
                continue;
            }
            let events: &[CompactEvent] = base.as_slice();
            // `--events wide` (CEO 26.09): сутки один раз в 64-байтные строки крейта; итог тот же.
            let n_events = events.len();
            let wide: Vec<hftbacktest::types::Event> = if args.events == "wide" {
                events.iter().map(CompactEvent::expand).collect()
            } else {
                Vec::new()
            };
            let rows = if args.events == "wide" {
                DayRows::Wide(&wide)
            } else {
                DayRows::Compact(events)
            };
            // S2: все формы над одним потоком событий, потоками; результат
            // каждой формы — сразу в дамп. Окна суток — один раз на все наборы.
            // Окна: при `--extra-runs` прогоны символ-суток делят окна. Нет всех своих `t0` в кэше —
            // строим для своих + кэшированных (одно объединение вместо отдельного прохода на прогон).
            let own_t0s: Vec<i64> = day
                .touches
                .iter()
                .map(|t| t.start_ms.saturating_mul(1_000_000))
                .collect();
            let cache_ok = shared.is_some() && !args.windows_check;
            let cached = if cache_ok {
                shared.as_ref().and_then(|s| {
                    s.windows
                        .get(&carry_key)
                        .filter(|e| e.1 == tick.to_bits() && e.2 == lot.to_bits())
                        .cloned()
                })
            } else {
                None
            };
            let windows: Option<Arc<SignalWindows>> = match cached {
                Some((t0s, _, _, w)) if own_t0s.iter().all(|t| t0s.binary_search(t).is_ok()) => {
                    eprintln!(
                        "bounce-grid:   окна: из кэша символ-суток ({} снимков)",
                        w.len()
                    );
                    Some(w)
                }
                other => {
                    let known: &[i64] = other.as_ref().map_or(&[], |e| e.0.as_slice());
                    let pre: &[i64] = shared
                        .as_ref()
                        .and_then(|s| s.pre_t0.get(&day.day))
                        .map_or(&[], Vec::as_slice);
                    let mut all: Vec<i64> =
                        own_t0s.iter().chain(known).chain(pre).copied().collect();
                    all.sort_unstable();
                    all.dedup();
                    let w = day_windows(rows, &all, args.driver, tick, lot, args.windows_check)?
                        .map(Arc::new);
                    if let (true, Some(w), Some(s)) = (cache_ok, w.as_ref(), shared.as_mut()) {
                        s.windows.insert(
                            carry_key.clone(),
                            (all, tick.to_bits(), lot.to_bits(), w.clone()),
                        );
                    }
                    w
                }
            };
            let regime = if need_regime {
                let dir = args.regime_from.as_deref().expect("проверено выше");
                if !regime_days.contains_key(&day.day) {
                    regime_days.insert(
                        day.day.clone(),
                        read_regime_day(dir, &day.day, sets.iter().any(FilterSet::uses_btc_mid))?,
                    );
                }
                regime_days.get(&day.day)
            } else {
                None
            };
            let ctx = touch_contexts(&day.rets, &day.touches, regime);
            // В-131: сколько сигналов суток без σ на взводе (до фильтров наборов) — в итог и строкой суток.
            if let Some(es) = &entry_sigma {
                let n = day.touches.len() as u64;
                let no = day
                    .touches
                    .iter()
                    .filter(|t| es.at(t.start_ms).is_none())
                    .count() as u64;
                summary.n_sigma_signals += n;
                summary.n_no_sigma += no;
                eprintln!("bounce-grid:   σ на взводе (В-131): без σ {no} из {n}");
            }
            let step_schedule = read_day_schedule(day_parts, tick_e9, step_e9)
                .map_err(|e| anyhow::anyhow!("{symbol} {}: {e}", day.day))?;
            // TK-049: урезанная лента суток — один раз на монето-сутки; без смены шага цены и только по окнам.
            let trim_band: Option<i64> = std::env::var("ALPHA_TRIM_ROWS")
                .ok()
                .and_then(|v| v.parse().ok());
            let kept: Vec<u32> = match (trim_band, rows, windows.as_ref(), step_schedule.as_ref()) {
                (Some(b), DayRows::Compact(c), Some(_), None) => {
                    let started = std::time::Instant::now();
                    let k = crate::lob::backtest::kept_rows(c, tick, lot, b);
                    eprintln!(
                        "bounce-grid:   урезанная лента (полоса {b}): {} из {} строк ({:.1} %) · {:.2}s",
                        k.len(),
                        c.len(),
                        100.0 * k.len() as f64 / c.len().max(1) as f64,
                        started.elapsed().as_secs_f64()
                    );
                    k
                }
                _ => Vec::new(),
            };
            let rows = match rows {
                DayRows::Compact(c) if !kept.is_empty() => DayRows::Trimmed(c, &kept),
                r => r,
            };
            let mut order_qtys = sizing.touch_qtys(&day.touches, tick_e9, args.order_qty_mult);
            if let Some(sc) = step_schedule.as_ref() {
                // В-172: размер ордера кратен лоту, действовавшему в момент касания (вверх — минимум не нарушаем).
                for (q, t) in order_qtys.iter_mut().zip(&day.touches) {
                    let lot_e9 = sc.at(t.start_ms.saturating_mul(1_000_000)).1;
                    if lot_e9 > 0 {
                        #[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
                        let q_e9 = (*q * 1e9).round() as i64;
                        let steps = (q_e9 + lot_e9 - 1) / lot_e9;
                        *q = (steps.saturating_mul(lot_e9)) as f64 / 1e9;
                    }
                }
            }
            let mut rounds: u64 = 0;
            let day_label = day.day.clone();
            // G10: память кругов на символ-сутки, по форме — наборы идут по очереди и берут
            // посчитанные круги готовыми. Один набор повторов не даёт — памяти нет.
            let memos: Vec<Mutex<RoundMemo>> = if windows.is_some()
                && ((args.round_memo == "on" && sets.len() > 1) || args.exit_group == "on")
            {
                (0..forms.len())
                    .map(|_| Mutex::new(RoundMemo::default()))
                    .collect()
            } else {
                Vec::new()
            };
            for ((set, out), &ids) in sets.iter().zip(outs.iter_mut()).zip(&set_form_ids) {
                let set_forms_list: Vec<GridForm> = ids.iter().map(|&i| forms[i]).collect();
                let forms_done = {
                    let forms_ref = &set_forms_list;
                    let mut sink =
                        |r: FormDayResult, signals: &[BounceSignal]| -> anyhow::Result<()> {
                            let n = out.write_form(
                                symbol,
                                &day_label,
                                forms_ref[r.form],
                                signals,
                                &r.run,
                                r.skipped,
                                carry_boundary_ns,
                                carry_unverified,
                                day.approaches.as_deref(),
                            )?;
                            rounds = rounds.saturating_add(n);
                            Ok(())
                        };
                    drive_day(
                        rows,
                        windows.as_deref(),
                        &day.touches,
                        day.approaches.as_deref(),
                        &set_forms_list,
                        DayParams {
                            memos: if memos.is_empty() {
                                None
                            } else {
                                Some(memos.as_slice())
                            },
                            form_ids: ids,
                            tick,
                            lot,
                            grid_e9: (tick_e9, step_e9),
                            step_schedule: step_schedule.as_ref(),
                            rtt_ns: args.median_rtt_ns,
                            queue_model,
                            busy_skip: args.busy_skip == "on",
                            hold_skip: args.hold_step == "skip",
                            exit_group: args.exit_group == "on",
                            order_qtys: &order_qtys,
                            threads,
                            post_only: args.entry_post_only(),
                            frontrun_only: set.frontrun_only,
                            min_age_ms: set.min_age_secs.map(|s| s.saturating_mul(1_000)),
                            min_flow_pct: set.min_flow_pct,
                            side: set.side.map(Side::from),
                            eaten_max_pct: set.eaten_max_pct,
                            eaten_min_pct: set.eaten_min_pct,
                            frontrun_min_lots: set.frontrun_min_lots,
                            usd_min: set.usd_min,
                            behind_min_pct: set.behind_min_pct,
                            stack_min: set.stack_min,
                            ctx: if set.uses_ctx() { Some(&ctx) } else { None },
                            ctx_ranges: set.ctx,
                            mode,
                            h3_usd: args.h3.h3_usd,
                            band_exit_bps,
                            sigma: &sigma_series,
                            entry_sigma: entry_sigma.as_ref(),
                        },
                        &mut sink,
                    )?
                };
                anyhow::ensure!(
                    forms_done == set_forms_list.len(),
                    "{symbol} {} {}: форм посчитано {}, ожидалось {}",
                    day.day,
                    set.name,
                    forms_done,
                    set_forms_list.len()
                );
            }
            summary.rounds = summary.rounds.saturating_add(rounds);
            summary.symbol_days += 1;
            eprintln!(
                "bounce-grid: {symbol} {} touches={} events={} forms={} rounds={} {:.1}s",
                day.day,
                day.touches.len(),
                n_events,
                forms.len(),
                rounds,
                day_started.elapsed().as_secs_f64()
            );
            // Р6: пересчёты кругов из-за короткого горизонта развёртки — строка только когда были
            // (прежний stderr не меняется); на итог не влияют, только на время.
            let retries = crate::lob::backtest::HORIZON_RETRIES
                .load(std::sync::atomic::Ordering::Relaxed)
                .saturating_sub(retries_before);
            if retries > 0 {
                eprintln!("bounce-grid:   горизонт развёртки: пересчётов кругов {retries}");
                if std::env::var_os("ALPHA_ATTEMPT_STATS").is_some() {
                    use crate::lob::backtest::{ATTEMPT_ROWS, ATTEMPT_RUNS};
                    let g = |a: &[std::sync::atomic::AtomicU64]| {
                        a.iter()
                            .map(|x| x.load(std::sync::atomic::Ordering::Relaxed).to_string())
                            .collect::<Vec<_>>()
                            .join("/")
                    };
                    eprintln!(
                        "bounce-grid:   попытки (0/1/2/3+, нарастающим итогом процесса): прогонов {} строк {}",
                        g(&ATTEMPT_RUNS),
                        g(&ATTEMPT_ROWS)
                    );
                    eprintln!(
                        "bounce-grid:   шаги кругов (без заявок/с заявками/прыжок, нарастающим итогом): {}",
                        g(&crate::lob::backtest::STEP_KINDS)
                    );
                    eprintln!(
                        "bounce-grid:   классы обновлений глубины (сдвиг лучшей/на лучшей/на стене/1..3 от лучшей/прочее/±3 от стены): {}",
                        g(&crate::lob::backtest::fast_depth::DEPTH_ROW_CLASS)
                    );
                    for (name, row) in ["вход", "выход-лимит"]
                        .iter()
                        .zip(&crate::lob::backtest::fast_depth::ORDER_DIST)
                    {
                        eprintln!(
                            "bounce-grid:   заявки {name} по удалению от лучшей (пересекает/0/1..3/4..10/>10 тиков): {}",
                            g(row)
                        );
                    }
                    eprintln!(
                        "bounce-grid:   строки глубины по удалению от лучшей (≤3/≤10/≤30/дальше тиков): {}",
                        g(&crate::lob::backtest::fast_depth::DEPTH_ROW_BANDS)
                    );
                }
            }
            // Э-04б: строка только при `--hold-step skip` (прежний stderr не меняется).
            let skips = crate::lob::backtest::HOLD_SKIPS
                .load(std::sync::atomic::Ordering::Relaxed)
                .saturating_sub(skips_before);
            if skips > 0 {
                eprintln!("bounce-grid:   удержание: пропусков пустых шагов {skips}");
            }
            if !memos.is_empty() {
                let (hits, misses) = memos.iter().fold((0u64, 0u64), |(h, m), x| {
                    let (a, b) = x.lock().map(|g| g.stats()).unwrap_or((0, 0));
                    (h + a, m + b)
                });
                eprintln!(
                    "bounce-grid:   память кругов: из памяти {hits}, посчитано движком {misses}"
                );
            }
        }
        summary.symbols_done += 1;
        eprintln!(
            "bounce-grid: {symbol} готов — суток {}, касаний {}, {:.1}s",
            days.iter().filter(|d| !d.touches.is_empty()).count(),
            touches_total,
            started.elapsed().as_secs_f64()
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests;
