//! `lob backtest` — CLI-обёртка вокруг движка `lob::backtest` (таск 11,
//! спека — истории 32–34, §6). Единственная зона, которой разрешено знать
//! одновременно про `Feed`/`bybit::ws` (площадка) и про `hftbacktest::types::
//! Event` (нейтральный формат крейта) — граница «чистое ядро стратегии не
//! тянет транспорт и площадку» держится тестом-грепом в `lob/backtest.rs`,
//! а перевод одного формата в другой поэтому живёт здесь, а не там.
//!
//! Вход — `ReplayFeed` по бинлогу одной сессии одного символа (таск 19:
//! `<SYMBOL>-<день>.binlog`, резолвер `super::session_binlog_for`;
//! `data/session-debug/<ts>/…`, `data/recon5/<SYMBOL>/…` — отладочные,
//! метка `debug`, `CLAUDE.md`). Сигналы (какие уровни торговать
//! и какой стороной) приходят отдельным CSV — этот файл их не размечает и
//! не классифицирует по осям семи профилей (это `lob levels`/будущая
//! проводка таска 12); здесь только: `profile_id,side,birth_ms` — сторона
//! `bid`/`ask` (Decision 14) и момент срабатывания в мс, тот же смысл, что
//! колонка `birth_ms` артефакта `lob levels`.
//!
//! Разрезка B3 (ревью 23.09): аргументы (`args.rs`), общие с `lob
//! bounce-grid` помощники движка — заголовок бинлога, `ReplayFeed`, перевод
//! `Feed` в события крейта, модель исполнения (`feed.rs`), размер круга и
//! поля лота пула (`pool.rs`), путь сравнения с таблицей профилей
//! (`profile_table.rs`), запись CSV (`csv_out.rs`), формы сделки-отскока
//! (`forms.rs`) и план сделки (`plan.rs`) — вынесены в подмодули; здесь —
//! точка входа (`run_backtest`) и путь `--touches` (`run_bounce`).

mod args;
mod csv_out;
mod feed;
mod forms;
mod plan;
mod pool;
mod profile_table;

pub use args::{BacktestArgs, BacktestSummary};
pub(crate) use csv_out::exit_reason_label;
use csv_out::{write_backtest_csv, write_pnl_csv, write_trades_csv};
use feed::events_from_paths;
pub use feed::BacktestFillModel;
pub(crate) use feed::{
    count_feed_events, count_feed_events_until, feed_events_into, feed_events_into_until,
    open_replay_feed, read_tick_step,
};
pub(crate) use forms::PlanShape;
pub use forms::{
    BounceForm, EntryForm, EntryTtl, StopForm, TakeForm, ENTRY_TTL_SECS, SINGLE_ENTRY_LABEL,
};
use plan::bounce_rows;
pub(crate) use plan::{
    approach_plan, bounce_plan, deadline_ns_from_secs, early_exit_ns_from_secs,
    touch_view_of_approach,
};
use pool::pool_order_qty;
pub(crate) use pool::PoolLot;
use profile_table::{read_signals, read_table};

use std::path::PathBuf;

use hftbacktest::types::Event as HbtEvent;

use crate::lob::backtest::{
    build_backtest, build_profile_report, drive_bounce, drive_profile, mean_net_bps, pnl_curve_bps,
    BacktestReport, BounceRun, BounceSignal, DriveConfig, QueueModelKind,
};
use crate::lob::costs::{net_fill_bps, net_fill_interval};
use crate::lob::levels::{LevelsConfig, TouchRecord};
use crate::lob::markout::MidSample;

/// Прогоняет бэктест на всех профилях `signals_csv` и пишет оба артефакта.
pub fn run_backtest(args: &BacktestArgs) -> anyhow::Result<BacktestSummary> {
    super::require_verified(&args.session_root, &args.symbol, args.allow_unverified)?;
    let binlog_paths = super::session_binlog_for(&args.session_root, &args.symbol)?;
    let (tick_e9, step_e9) = read_tick_step(&binlog_paths[0])?;
    let tick_size = tick_e9 as f64 / 1e9;
    let lot_size = step_e9 as f64 / 1e9;

    // Таск 22: сессия может нести несколько частей — читаются подряд как
    // один поток (`events_from_paths`), не только первая.
    let events = events_from_paths(&binlog_paths)?;
    if events.is_empty() {
        let names = binlog_paths
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(", ");
        anyhow::bail!("бинлог(и) {names} пусты или не разобрались");
    }

    // Сделка-отскока (В-44, таск 38): сигналы приходят не из CSV, а из
    // касаний живых уровней того же реплея — отдельная ветка, потому что и
    // план у каждой сделки свой, и строки отчёта — оси В-44.
    if args.touches {
        return run_bounce(args, &binlog_paths, tick_e9, step_e9, &events);
    }
    // Профильный путь: лот задаётся флагом (лот из пула считается по цене
    // касаний, которых здесь нет) — разрешение после ветки `--touches`.
    let order_qty_e9 = order_qty_arg(args)?;
    let order_qty = order_qty_e9 as f64 / 1e9;
    let Some(signals_csv) = args.signals_csv.as_deref() else {
        anyhow::bail!("нужен либо --signals <csv>, либо --touches");
    };
    let signals = read_signals(signals_csv)?;
    if signals.is_empty() {
        anyhow::bail!("сигналов нет: {}", signals_csv.display());
    }
    let table = read_table(args.profiles_csv.as_deref())?;

    let cfg = DriveConfig {
        order_qty,
        first_order_id: 1,
        // `lob backtest` (профили / `--touches`) — прежний движок: модель
        // очереди выбирается только у сетки форм (`--queue-model`, F3).
        queue_model: QueueModelKind::RiskAdverse,
    };

    let mut reports = Vec::with_capacity(signals.len());
    for (profile_id, sigs) in &signals {
        let mut median_bt = build_backtest(
            &events,
            tick_size,
            lot_size,
            args.median_rtt_ns,
            cfg.queue_model,
        );
        let median = drive_profile(&mut median_bt, 0, sigs, &cfg)?;
        let mut p95_bt = build_backtest(
            &events,
            tick_size,
            lot_size,
            args.p95_rtt_ns,
            cfg.queue_model,
        );
        let p95 = drive_profile(&mut p95_bt, 0, sigs, &cfg)?;
        let estimate = table.get(profile_id).copied();
        reports.push(build_profile_report(
            profile_id.clone(),
            median,
            p95,
            estimate,
        ));
    }
    let report = BacktestReport::new(reports);

    let date = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/backtest-{date}.csv")));
    let pnl_out = args
        .pnl_out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/backtest-{date}-pnl.csv")));

    let header = header_comment(args, order_qty_e9);
    write_backtest_csv(&out, &report, &header)?;
    write_pnl_csv(&pnl_out, &report, &header)?;

    for line in report.summary_lines() {
        println!("{line}");
    }

    let pass = report.profiles.iter().filter(|p| p.g4.is_pass()).count();
    let red = report.profiles.len() - pass;
    Ok(BacktestSummary {
        profiles: report.profiles.len(),
        pass,
        red,
        out,
        pnl_out,
    })
}

// ---------------------------------------------------------------------------
// Артефакты: сводка и кривые PnL.
// ---------------------------------------------------------------------------

/// Шапка обоих артефактов (координатор): те же параметры, что реально
/// решают число, плюс `debug` — по решению вызывающего (`--debug`), тем же
/// приёмом, что `lob profiles --allow-unverified`.
fn header_comment(args: &BacktestArgs, order_qty_e9: i64) -> String {
    let debug_suffix = if args.debug { " debug" } else { "" };
    format!(
        "# lob backtest: rtt_median_ns={} rtt_p95_ns={} order_qty_e9={} alpha={} replications={} seed=0{debug_suffix}",
        args.median_rtt_ns,
        args.p95_rtt_ns,
        order_qty_e9,
        crate::stats::GATE_ALPHA,
        crate::stats::BOOTSTRAP_REPLICATIONS,
    )
}

/// Явный размер круга профильного пути. Лот из пула (`--order-qty-from-pool`)
/// считается по цене касаний реплея, а профильный путь касаний не строит:
/// отказ, а не подстановка шага книги вместо лота площадки.
fn order_qty_arg(args: &BacktestArgs) -> anyhow::Result<i64> {
    match (args.order_qty_e9, args.order_qty_from_pool) {
        (Some(v), false) => positive_order_qty(v),
        (None, false) => anyhow::bail!(
            "нужен --order-qty-e9 (либо --order-qty-from-pool вместе с --touches): \
             изобретённого умолчания нет (§9 плана)"
        ),
        (_, true) => anyhow::bail!(
            "--order-qty-from-pool работает только с --touches: цену для `order_size_22a` \
             даёт реплей касаний"
        ),
    }
}

/// Явный лот обязан быть положительным (ревью 24.09, блок A): `drive_signal`
/// проверяет размер круга только `debug_assert`, и в релизной сборке нулевой
/// `--order-qty-e9` молча гнал бы круги без размера — тот же отказ, что у
/// сетки форм (`OrderSizing`, `31a307b`).
fn positive_order_qty(v: i64) -> anyhow::Result<i64> {
    anyhow::ensure!(
        v > 0,
        "--order-qty-e9 {v} не положителен — круг без размера"
    );
    Ok(v)
}

/// Прогон сделки-отскока по касаниям символа и отчёт по осям В-44.
fn run_bounce(
    args: &BacktestArgs,
    binlog_paths: &[PathBuf],
    tick_e9: i64,
    step_e9: i64,
    events: &[HbtEvent],
) -> anyhow::Result<BacktestSummary> {
    let tick = tick_e9 as f64 / 1e9;
    let lot_size = step_e9 as f64 / 1e9;
    // Дедлайн и досрочный выход — из предрегистрированных сеток В-58 (B3/B4):
    // другое значение отказ, а не молчаливое расширение сетки.
    let deadline_ns = deadline_ns_from_secs(args.deadline_secs)?;
    let early_exit_ns = early_exit_ns_from_secs(args.early_exit_secs)?;

    // Порог `H3` — тем же резолвером, что `lob touches`/`levels`: числа
    // считаются тем же кодом, что CSV касаний.
    let mode = super::resolve_h3_mode_with_k(
        &args.session_root,
        &args.symbol,
        args.h3.h3_mode,
        args.h3.h3_lots,
        args.h3_k,
    )?;
    let cfg_levels = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms.unwrap_or(super::DEFAULT_WARMUP_MS),
        repeat_window_ms: args
            .repeat_window_ms
            .unwrap_or(super::DEFAULT_REPEAT_WINDOW_MS),
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let h3_lots = mode
        .single_h3_lots()
        .ok_or_else(|| anyhow::anyhow!("режим H3 без единого порога в лотах (notional/strength/both) здесь не поддерживается: оси «×H3» не определены"))?;
    // Форма сделки — из базы (В-65); без имён ветка `--touches` не идёт.
    let form = match (args.stop_form.as_deref(), args.take_form.as_deref()) {
        (Some(stop), Some(take)) => BounceForm::parse(stop, take, args.take_floor_fees)?,
        _ => anyhow::bail!(
            "--touches требует --stop-form и --take-form (формы базы В-65; умолчаний нет)"
        ),
    };
    let replay = super::replay_symbol(&args.session_root, &args.symbol, cfg_levels)?;
    let mut touches: Vec<TouchRecord> = Vec::new();
    let mut mids: Vec<MidSample> = Vec::new();
    for day in &replay.days {
        touches.extend(day.touches.iter().cloned());
        mids.extend(day.mids.iter().copied());
    }
    // Ряд σ — по всей записи символа, чтобы окно раннего касания вторых суток
    // смотрело в первые (`lob::sigma`).
    let sigma_series = crate::lob::sigma::SigmaSeries::from_mids(&mids);
    drop(mids);
    anyhow::ensure!(
        !touches.is_empty(),
        "касаний нет в {}: разметка пуста или порог не дал уровней",
        args.session_root.display()
    );

    // План на каждое касание по форме; касание, для которого форму не
    // построить (нет σ, фронтрана, второй плотности — `bounce_plan` → `None`),
    // выбывает **до** осей и индексов, чтобы круг относился к касанию по тому
    // же порядку, что ниже.
    let deadline_secs = args.deadline_secs;
    let mut skipped = 0usize;
    let (touches, signals): (Vec<TouchRecord>, Vec<BounceSignal>) = touches
        .into_iter()
        .filter_map(|t| {
            let sigma_bps = sigma_series.sigma_bps(t.start_ms, deadline_secs);
            let built = bounce_plan(
                &t,
                tick,
                form,
                sigma_bps,
                PlanShape {
                    lot: lot_size,
                    post_only: args.post_only,
                    trail_bps: args.trail_bps,
                    trail_activate_bps: args.trail_activate_bps,
                    grid_legs: args.grid_legs,
                    grid_step_ticks: args.grid_step_ticks,
                    // F6: у одиночного `lob backtest --touches` вход прежний
                    // (`single@fr`); лестница формы живёт в сетке
                    // (`lob bounce-grid --entry-form`), свой флаг ей там.
                    entry_form: EntryForm::SingleFrontrun,
                    deadline_ns,
                    early_exit_ns,
                    // Одиночный `lob backtest --touches` — прежний режим
                    // входа «до конца касания»: условия F5 живут у сетки
                    // (`lob bounce-grid --entry-ttl-secs`), здесь их нет.
                    entry_ttl: EntryTtl::Touch,
                    h3_usd: None,
                    band_exit_bps: 0.0,
                    // F7/F8: форма выхода — одиночный backtest не использует ось выхода.
                    exit_form: crate::commands::lob::bounce_grid::ExitForm::None,
                },
            );
            match built {
                Some((sigma, plan)) => {
                    let signal = BounceSignal {
                        t0_ns: t.start_ms.saturating_mul(1_000_000),
                        sigma,
                        plan,
                        profile: 0,
                        qty: None,
                    };
                    Some((t, signal))
                }
                None => {
                    skipped += 1;
                    None
                }
            }
        })
        .unzip();
    anyhow::ensure!(
        !touches.is_empty(),
        "ни для одного касания форма {}-{} не строится (пропущено {skipped})",
        form.stop.label(),
        form.take.label()
    );
    let mut order: Vec<usize> = (0..signals.len()).collect();
    order.sort_by_key(|&i| signals[i].t0_ns);

    // Размер круга — явный флаг или `order_size_22a` от пула и цены последнего
    // касания (Decision 22а). Решается здесь, а не в начале: цену даёт реплей
    // касаний, который идёт выше.
    let order_qty_e9 = match (args.order_qty_e9, args.order_qty_from_pool) {
        (Some(v), false) => positive_order_qty(v)?,
        (None, true) => {
            let last = touches
                .last()
                .expect("касаний нет — отказ раньше, до сигналов");
            pool_order_qty(
                &args.session_root.join("instruments.csv"),
                &args.symbol,
                last.price_tick,
                tick_e9,
            )?
        }
        (Some(_), true) => anyhow::bail!(
            "--order-qty-e9 и --order-qty-from-pool взаимоисключающие: лот задаётся одним способом"
        ),
        (None, false) => anyhow::bail!(
            "нужен --order-qty-e9 или --order-qty-from-pool: изобретённого умолчания нет (§9 плана)"
        ),
    };
    let order_qty = order_qty_e9 as f64 / 1e9;

    let cfg = DriveConfig {
        order_qty,
        first_order_id: 1,
        // Одиночный `lob backtest --touches` — прежний движок: модель очереди
        // выбирается только у сетки форм (`lob bounce-grid --queue-model`, F3).
        queue_model: QueueModelKind::RiskAdverse,
    };
    let mut bt = build_backtest(events, tick, lot_size, args.median_rtt_ns, cfg.queue_model);
    let run: BounceRun = drive_bounce(&mut bt, 0, &signals, &cfg)?;

    let rows = bounce_rows(&touches, h3_lots);
    let date = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/bounce-backtest-{date}.csv")));
    let pnl_out = args
        .pnl_out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/bounce-backtest-{date}-pnl.csv")));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    // Кривая PnL — такой же артефакт прогона: свой каталог она создаёт сама,
    // как профильный путь и покруговой дамп (иначе прогон в свежий каталог
    // падал бы «путь не найден» уже после записи сводки).
    if let Some(parent) = pnl_out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }

    let header = format!(
        "# lob backtest --touches: {} {} RTT={}нс {}, сделка-отскок В-44/В-65 (вход от первого фронтранера, иначе за тик; стоп {}, тейк {}, пол σ-тейка {:?} кругов комиссий, σ_H — за окно дедлайна; дедлайн {} мс из сетки В-58, досрочный выход {}), порог H3={} лотов, касаний {} (форма не построилась у {skipped}), бинлогов {}",
        args.symbol,
        args.session_root.display(),
        args.median_rtt_ns,
        args.median_rtt_ns.provenance(),
        form.stop.label(),
        form.take.label(),
        form.take_floor_fees,
        args.deadline_secs.saturating_mul(1_000),
        match args.early_exit_secs {
            Some(x) => format!("по прилипанию {x} с (сетка В-58)"),
            None => "выключен".to_string(),
        },
        h3_lots,
        touches.len(),
        binlog_paths.len()
    );
    // Шапка артефакта — строкой комментария: чем посчитан файл (`rtt=assumed`
    // В-37, порог `H3`, состав сделки). Пишется до таблицы, как у остальных
    // артефактов вердикта.
    if let Some(trades_out) = args.trades_out.as_ref() {
        write_trades_csv(trades_out, &header, &run)?;
    }
    let mut file = std::fs::File::create(&out)?;
    use std::io::Write as _;
    // `header` уже начинается с `#`: дописывать второй значило бы печатать
    // `# #` в каждом артефакте вердикта.
    writeln!(file, "{header}")?;
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "profile_id",
        "n_signals",
        "n_submitted",
        "n_filled",
        "fill",
        "n_busy",
        "n_entry_timeout",
        "n_stop",
        "n_take",
        "n_timeout",
        "n_trail",
        "n_early",
        "net_bps",
        "net_fill_bps",
        "net_fill_lo_bps",
        "net_fill_point_bps",
        "days",
        "incomplete",
    ])?;
    let mut in_pos: Vec<usize> = vec![0; signals.len()];
    for (pos, &idx) in order.iter().enumerate() {
        in_pos[idx] = pos;
    }
    let mut all_fills: Vec<crate::lob::backtest::Fill> = Vec::new();
    for row in &rows {
        let positions: Vec<usize> = row.touches.iter().map(|&i| in_pos[i]).collect();
        let mut obs = Vec::with_capacity(positions.len());
        for &p in &positions {
            if let Some(o) = run.observations.get(p) {
                obs.push(*o);
            }
        }
        let fills: Vec<crate::lob::backtest::Fill> = run
            .fills
            .iter()
            .zip(&run.fill_signal)
            .filter(|(_, &s)| positions.contains(&s))
            .map(|(f, _)| *f)
            .collect();
        let reasons: Vec<&crate::lob::strategy::ExitReason> = run
            .fill_reason
            .iter()
            .zip(&run.fill_signal)
            .filter(|(_, &s)| positions.contains(&s))
            .map(|(r, _)| r)
            .collect();
        let n_stop = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Stop))
            .count();
        let n_take = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Take))
            .count();
        let n_timeout = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Deadline))
            .count();
        let n_trail = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Trail))
            .count();
        let n_early = reasons
            .iter()
            .filter(|r| matches!(r, crate::lob::strategy::ExitReason::Early))
            .count();
        let interval = net_fill_interval(
            &obs,
            crate::stats::GATE_ALPHA,
            crate::stats::BOOTSTRAP_REPLICATIONS,
            0,
        );
        let num = |v: Option<f64>| match v {
            Some(x) => format!("{x:.6}"),
            None => "—".to_string(),
        };
        let mut days: Vec<i64> = obs.iter().map(|o| o.day_cluster).collect();
        days.sort_unstable();
        days.dedup();
        // Доля исполнения — от **отправленных** входов, а не от всех касаний:
        // касание, пропущенное из-за занятой позиции, не «неисполненный
        // вход» (находка прогона 2026-09-13, смешивать их нельзя).
        let n_submitted = positions
            .iter()
            .filter(|p| run.submitted_signal.binary_search(p).is_ok())
            .count();
        let n_busy = positions
            .iter()
            .filter(|p| run.busy_signal.binary_search(p).is_ok())
            .count();
        let fill_share = if n_submitted > 0 {
            Some(fills.len() as f64 / n_submitted as f64)
        } else {
            None
        };
        w.write_record([
            row.profile.clone(),
            positions.len().to_string(),
            n_submitted.to_string(),
            fills.len().to_string(),
            num(fill_share),
            n_busy.to_string(),
            n_submitted.saturating_sub(fills.len()).to_string(),
            n_stop.to_string(),
            n_take.to_string(),
            n_timeout.to_string(),
            n_trail.to_string(),
            n_early.to_string(),
            num(mean_net_bps(&fills)),
            num(net_fill_bps(&obs)),
            num(interval.map(|i| i.lower_bps)),
            num(interval.map(|i| i.point_bps)),
            days.len().to_string(),
            run.incomplete.to_string(),
        ])?;
        if row.profile == "все" {
            all_fills = fills;
        }
    }
    w.flush()?;

    // Кривая PnL — по всем кругам прогона (одна на символ, не на ось).
    let mut wp = csv::Writer::from_path(&pnl_out)?;
    wp.write_record(["n", "cum_net_bps"])?;
    if let Some(curve) = pnl_curve_bps(&all_fills) {
        for (i, v) in curve.iter().enumerate() {
            wp.write_record([(i + 1).to_string(), format!("{v:.6}")])?;
        }
    }
    wp.flush()?;

    // Замеры механизма (таск 38): почему вход не исполняется — по книге и по
    // статусам ордеров, а не по догадке из чужого исходника.
    let mut hist = [0u64; 4]; // спред 1, 2, 3, 4+ тика
    for s in &run.spread_at_entry {
        let t = (s / tick).round().max(1.0) as usize;
        hist[(t - 1).min(3)] += 1;
    }
    let sent = run.spread_at_entry.len().max(1) as f64;
    println!(
        "bounce: вход отправлен {} раз · пересекал спред {} ({:.1}%) · отвергнуто биржей {} · спред на входе: 1 тик {:.1}%, 2 {:.1}%, 3 {:.1}%, ≥4 {:.1}%",
        run.spread_at_entry.len(),
        run.entry_crossed,
        100.0 * run.entry_crossed as f64 / sent,
        run.entry_rejected,
        100.0 * hist[0] as f64 / sent,
        100.0 * hist[1] as f64 / sent,
        100.0 * hist[2] as f64 / sent,
        100.0 * hist[3] as f64 / sent,
    );
    println!(
        "bounce: промахи раздельно — вход не исполнен {} · позиция занята {} · самая долгая блокировка {:.1} с · самая долгая жизнь круга {:.1} с",
        run.misses.timeout,
        run.misses.busy,
        run.busy_wait_ns_max as f64 / 1e9,
        run.round_ns_max as f64 / 1e9,
    );
    println!(
        "bounce: касаний {} · кругов {} · стоп {} · тейк {} · трейл {} · дедлайн {} · досрочно {} · съедено {} · частями {} · промахи {} · incomplete {}",
        touches.len(),
        run.fills.len(),
        run.exits.stop,
        run.exits.take,
        run.exits.trail,
        run.exits.deadline,
        run.exits.early,
        run.exits.eaten,
        run.exits.partial,
        run.misses.total(),
        run.incomplete
    );
    println!(
        "bounce: строк {} → {} (+ кривые PnL {})",
        rows.len(),
        out.display(),
        pnl_out.display()
    );

    let pass = 0;
    Ok(BacktestSummary {
        profiles: rows.len(),
        pass,
        red: rows.len(),
        out,
        pnl_out,
    })
}

#[cfg(test)]
mod tests;
