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
mod forms;
mod outputs;
mod plan;
mod sets;

use args::SignalArg;
pub use args::{BounceGridArgs, BounceGridSummary};
pub(crate) use cache::cached_touches;
use cache::{cached_approaches, DayTouches};
use carry::{carry_window_ns, extend_with_carry};
use drive::{day_events, day_windows, drive_day, DayParams, OrderSizing};
pub use forms::{grid_forms, ExitForm, GridForm};
use outputs::FormDayResult;
pub(crate) use plan::{ensure_holds_at_touch_checkable, pool_symbols};
use plan::{open_outputs, plan_grid, GridPlan};
use sets::SetPaths;
pub(crate) use sets::{read_regime_day, touch_contexts, FilterSet, RegimeDay, TouchFilter};

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Instant;

use super::backtest::read_tick_step;
use super::profiles::read_verify_marker;
use super::{
    replay_symbol_touches_and_second_mids, resolve_h3_mode_full, session_parts_for,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::book::Side;
use crate::lob::backtest::{BounceSignal, RoundMemo};
use crate::lob::levels::LevelsConfig;
use crate::lob::sigma::SigmaSeries;

pub fn run_bounce_grid(args: &BounceGridArgs) -> anyhow::Result<BounceGridSummary> {
    let plan = plan_grid(args)?;
    let mut outs = open_outputs(args, &plan)?;
    let GridPlan {
        queue_model,
        threads,
        deadlines,
        entry_ttls,
        band_exit_bps,
        forms,
        sets,
        need_regime,
        need_ret,
        symbols,
        ..
    } = plan;
    // Режим суток читается один раз на сутки (общий для символов).
    let mut regime_days: BTreeMap<String, RegimeDay> = BTreeMap::new();
    // Перенос круга через полночь (`--carry-root`): окно — один раз на прогон,
    // те же числа сетки для всех символов и суток (`carry_window_ns`).
    let carry_window = match &args.carry_root {
        Some(_) => Some(carry_window_ns(
            &entry_ttls,
            &deadlines,
            args.p95_rtt_ns.taker_ns,
        )?),
        None => None,
    };

    let mut summary = BounceGridSummary {
        forms: forms.len(),
        rounds_path: outs[0].rounds_path.clone(),
        forms_path: outs[0].forms_path.clone(),
        sets: sets
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
                        continue;
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
                continue;
            };
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
                    continue;
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
            continue;
        }
        let sizing = OrderSizing::from_args(args, symbol)?;
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
            // S4: события одних суток, не всей сессии.
            let mut events = day_events(day_parts)?;
            if events.is_empty() {
                eprintln!(
                    "bounce-grid: {symbol} {} — событий нет, сутки пропущены",
                    day.day
                );
                continue;
            }
            // Довесок (`--carry-root`): дописывает события D+1 в окне переноса
            // ДО построения окон сетапов — тот же приём, что уже склеивает
            // части одних суток (`day_events`: части хронологичны, каждая
            // несёт свой снапшот, поэтому конкатенация корректна без ручной
            // сшивки книги). Сигналы дня (`day.touches` ниже) от довеска не
            // зависят — он только дописывает хвост потока книги/сделок.
            let (carry_boundary_ns, carry_unverified) = match carry_window {
                Some(window) => extend_with_carry(
                    &mut events,
                    &day.day,
                    args.carry_root.as_deref(),
                    &carry_parts_by_day,
                    symbol,
                    window,
                )?,
                None => (None, false),
            };
            // S2: все формы над одним потоком событий, потоками; результат
            // каждой формы — сразу в дамп. Окна суток — один раз на все наборы.
            let windows = day_windows(&events, &day.touches, args.driver, tick, lot);
            let regime = if need_regime {
                let dir = args.regime_from.as_deref().expect("проверено выше");
                if !regime_days.contains_key(&day.day) {
                    regime_days.insert(day.day.clone(), read_regime_day(dir, &day.day)?);
                }
                regime_days.get(&day.day)
            } else {
                None
            };
            let ctx = touch_contexts(&day.rets, &day.touches, regime);
            let order_qtys = sizing.touch_qtys(&day.touches, tick_e9, args.order_qty_mult);
            let mut rounds: u64 = 0;
            let day_label = day.day.clone();
            // G10: память кругов на символ-сутки, по форме — наборы идут по очереди и берут
            // посчитанные круги готовыми. Один набор повторов не даёт — памяти нет.
            let memos: Vec<Mutex<RoundMemo>> =
                if windows.is_some() && args.round_memo == "on" && sets.len() > 1 {
                    (0..forms.len())
                        .map(|_| Mutex::new(RoundMemo::default()))
                        .collect()
                } else {
                    Vec::new()
                };
            for (set, out) in sets.iter().zip(outs.iter_mut()) {
                let forms_done = {
                    let forms_ref = &forms;
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
                            )?;
                            rounds = rounds.saturating_add(n);
                            Ok(())
                        };
                    drive_day(
                        &events,
                        windows.as_ref(),
                        &day.touches,
                        day.approaches.as_deref(),
                        &forms,
                        DayParams {
                            memos: if memos.is_empty() {
                                None
                            } else {
                                Some(memos.as_slice())
                            },
                            tick,
                            lot,
                            rtt_ns: args.median_rtt_ns,
                            queue_model,
                            order_qtys: &order_qtys,
                            threads,
                            post_only: args.entry_post_only(),
                            frontrun_only: set.frontrun_only,
                            min_age_ms: set.min_age_secs.map(|s| s.saturating_mul(1_000)),
                            min_flow_pct: set.min_flow_pct,
                            side: set.side.map(Side::from),
                            eaten_max_pct: set.eaten_max_pct,
                            usd_min: set.usd_min,
                            ctx: if set.uses_ctx() { Some(&ctx) } else { None },
                            ctx_ranges: set.ctx,
                            mode,
                            h3_usd: args.h3.h3_usd,
                            band_exit_bps,
                            sigma: &sigma_series,
                        },
                        &mut sink,
                    )?
                };
                anyhow::ensure!(
                    forms_done == forms.len(),
                    "{symbol} {} {}: форм посчитано {}, ожидалось {}",
                    day.day,
                    set.name,
                    forms_done,
                    forms.len()
                );
            }
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
    }
    Ok(summary)
}

#[cfg(test)]
mod tests;
