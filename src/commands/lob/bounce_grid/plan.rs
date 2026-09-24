//! Планирование прогона сетки: проверка окна порога силы
//! (`ensure_holds_at_touch_checkable`), разбор и проверка флагов
//! `BounceGridArgs` в `GridPlan` (`plan_grid`), список символов пула
//! (`pool_symbols`) и открытие артефактов набора (`open_outputs`).
//! Вынесено из `bounce_grid` при разрезке B3 (ревью 23.09), поведение не
//! менялось.

use std::io::Write;
use std::path::Path;

use crate::book::Side;
use crate::commands::lob::backtest::{BounceForm, EntryForm, EntryTtl, StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::{DEADLINE_SECS, DEADLINE_SECS_ALLOWED};
use crate::lob::backtest::QueueModelKind;
use crate::lob::levels::{H3Mode, TouchRecord};

use super::args::{
    ensure_entry_conditions_args, parse_early_exits, parse_entry_forms, parse_entry_ttls,
    parse_exit_forms, BounceGridArgs, SideArg, SignalArg,
};
use super::forms::{grid_forms_with_early, ExitForm, GridForm};
use super::outputs::Outputs;
use super::sets::FilterSet;

pub(crate) fn pool_symbols(root: &Path) -> anyhow::Result<Vec<String>> {
    let path = root.join("instruments.csv");
    let mut r = crate::commands::lob::pick::instruments_csv_reader(&path)
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

/// Окно силы порога обязано быть одним из окон оси `strength_e2`, иначе
/// порог в момент касания (`H3Mode::holds_at_touch`) не проверить — отказ,
/// не молчаливый пропуск. Общая проверка сетки и замера ёмкости.
pub(crate) fn ensure_holds_at_touch_checkable(mode: &H3Mode, symbol: &str) -> anyhow::Result<()> {
    let probe = TouchRecord {
        side: Side::Bid,
        price_tick: 1,
        touch_index: 0,
        start_ms: 0,
        end_ms: 0,
        duration_ms: 0,
        level_birth_ms: 0,
        size_at_touch: i64::MAX / 4,
        size_max_before: 0,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 0,
        ended_by_death: false,
        stack_levels: 0,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [i64::MAX; 3],
        strength_held_e2: [-1; 4],
        repeat_count: 0,
    };
    anyhow::ensure!(
        mode.holds_at_touch(&probe).is_some(),
        "{symbol}: окно силы порога не из STRENGTH_WINDOWS_BPS {:?} — порог в момент касания не проверить",
        crate::lob::levels::STRENGTH_WINDOWS_BPS
    );
    Ok(())
}

/// Разобранные и проверенные флаги одного прогона сетки (аудит 21.09, С6:
/// вынесено из `run_bounce_grid`, чтобы разбор, открытие артефактов и цикл по
/// символам читались отдельно). Числа — только из флагов, умолчаний здесь нет.
pub(crate) struct GridPlan {
    pub(crate) queue_model: QueueModelKind,
    pub(crate) threads: usize,
    pub(crate) deadlines: Vec<u64>,
    pub(crate) entry_ttls: Vec<EntryTtl>,
    pub(crate) band_exit_bps: f64,
    pub(crate) entries: Vec<EntryForm>,
    pub(crate) exits: Vec<ExitForm>,
    pub(crate) forms: Vec<GridForm>,
    pub(crate) sets: Vec<FilterSet>,
    pub(crate) need_regime: bool,
    pub(crate) need_ret: bool,
    pub(crate) symbols: Vec<String>,
}

/// Разбор и проверка флагов `bounce-grid`: формы, дедлайны, срок жизни входа,
/// наборы фильтров, сигнал, режим, символы. Все отказы — здесь, до чтения данных.
pub(crate) fn plan_grid(args: &BounceGridArgs) -> anyhow::Result<GridPlan> {
    anyhow::ensure!(
        args.root.is_dir(),
        "{}: корень записи не каталог",
        args.root.display()
    );
    let lot_sources = usize::from(args.order_qty_e9.is_some())
        + usize::from(args.order_qty_from_pool)
        + usize::from(args.order_usd.is_some());
    anyhow::ensure!(
        lot_sources == 1,
        "лот задаётся ровно одним способом: --order-qty-e9 | --order-qty-from-pool | --order-usd (изобретённого умолчания нет, §9 плана)"
    );
    // Модель очереди/исполнения (F3): обязательный флаг, разбирается один раз
    // на процесс — она не часть фильтров набора (`--set`), а движок.
    let queue_model = args.queue_model;
    let threads = args
        .threads
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1)
        })
        .max(1);
    // Формы базы (В-65) — имена владельца: каждая разбирается и проверяется,
    // повтор имени — отказ (это было бы лишнее «испытание» с тем же именем).
    let stops = args
        .stop_form
        .iter()
        .map(|s| StopForm::parse(s))
        .collect::<anyhow::Result<Vec<_>>>()?;
    let takes = args
        .take_form
        .iter()
        .map(|s| TakeForm::parse(s))
        .collect::<anyhow::Result<Vec<_>>>()?;
    for &stop in &stops {
        for &take in &takes {
            BounceForm {
                stop,
                take,
                take_floor_fees: args.take_floor_fees,
            }
            .validate()?;
        }
    }
    let deadlines: Vec<u64> = if args.deadline_secs.is_empty() {
        DEADLINE_SECS.to_vec()
    } else {
        for &d in &args.deadline_secs {
            anyhow::ensure!(
                DEADLINE_SECS_ALLOWED.contains(&d),
                "--deadline-secs {d}: не из разрешённых {DEADLINE_SECS_ALLOWED:?}"
            );
        }
        let mut d = args.deadline_secs.clone();
        d.sort_unstable();
        d.dedup();
        d
    };
    // Срок жизни входа (F5, В-74): сетка режимов из повторяемого
    // `--entry-ttl-secs`; условия рынка требуют порога В-66 в деньгах
    // (`--h3-usd`) и полосы ухода (`--band-exit-bps`) — числа замера, не
    // умолчания (`ensure_entry_conditions_args`).
    let entry_ttls = parse_entry_ttls(&args.entry_ttl_secs)?;
    let entry_conditions = entry_ttls.iter().any(|t| *t != EntryTtl::Touch);
    ensure_entry_conditions_args(entry_conditions, args.h3.h3_usd, args.band_exit_bps)?;
    let band_exit_bps = match args.band_exit_bps {
        Some(v) => {
            anyhow::ensure!(
                v.is_finite() && v > 0.0,
                "--band-exit-bps {v}: полоса ухода — конечное число > 0"
            );
            v
        }
        // Прежний режим (`touch`): условие выключено, число не читается.
        None => 0.0,
    };
    let entries = parse_entry_forms(&args.entry_form)?;
    let exits = parse_exit_forms(&args.exit_form)?;
    let earlies = parse_early_exits(&args.early_exit_secs)?;
    let forms = grid_forms_with_early(
        &stops,
        &takes,
        args.take_floor_fees,
        &deadlines,
        &entry_ttls,
        &entries,
        &exits,
        &earlies,
    );
    {
        let labels: std::collections::BTreeSet<&str> = forms.iter().map(|f| f.label).collect();
        anyhow::ensure!(
            labels.len() == forms.len(),
            "сетка: повторяющиеся формы в --stop-form/--take-form/--entry-ttl-secs/--entry-form/--exit-form/--early-exit-secs дают одинаковые имена"
        );
    }
    // F6 (В-73): сигнал по записи подхода — только из кэша F1, реплея
    // подходов у сетки нет (полосу `D` выбирает прогон `lob touches
    // --approach-bps`; в часы ночи их пишет шаг H3).
    if args.signal == SignalArg::Approach {
        anyhow::ensure!(
            args.touches_from.is_some(),
            "--signal approach: нужен кэш подходов --touches-from <dir> (approaches-<SYMBOL>.csv, F1) — реплей подходов сетка не считает"
        );
    }
    if let Some(dir) = &args.touches_from {
        anyhow::ensure!(dir.is_dir(), "--touches-from {}: не каталог", dir.display());
        anyhow::ensure!(
            !forms.iter().any(|f| f.form.needs_sigma()),
            "--touches-from: σ-формы (s<a>/t<b>) требуют реплея — σ в CSV касаний суточная и с шестью знаками"
        );
        anyhow::ensure!(
            args.warmup_ms.is_none() && args.repeat_window_ms.is_none(),
            "--touches-from: прогрев и окно повтора трекера — умолчания, как у ночного `lob touches`"
        );
    }
    let sets: Vec<FilterSet> = if args.sets.is_empty() {
        vec![FilterSet::from_args(args)]
    } else {
        anyhow::ensure!(
            !args.frontrun_only
                && args.min_age_secs.is_none()
                && args.min_flow_pct.is_none()
                && args.side.is_none(),
            "--set задан: фильтры касаний только в наборах, флаги --min-age-secs/--min-flow-pct/--side/--frontrun-only у команды — отказ"
        );
        let parsed = args
            .sets
            .iter()
            .map(|s| FilterSet::parse(s))
            .collect::<anyhow::Result<Vec<_>>>()?;
        let names: std::collections::BTreeSet<&str> =
            parsed.iter().map(|s| s.name.as_str()).collect();
        anyhow::ensure!(
            names.len() == parsed.len(),
            "--set: имена наборов повторяются"
        );
        parsed
    };
    // F6 (В-73): у записи подхода нет ни истории размера (ключ `eaten=`), ни
    // хода **монеты** до взвода (ключи `ret*` — их несёт только кэш касаний) —
    // фильтры, которые их читают, молча выбросили бы все сигналы; отказ, как
    // у F2. Режим (`pool*`/`btc*`, F10b) у подхода определён: он читается из
    // `--regime-from` по минуте `start_ms`, а у подхода это минута взвода
    // (`touch_view_of_approach`: `start_ms = arm_ms`) — так H1 «лонг после
    // просадки BTC» (`btc4h_max=0`) проверяется и на подходах.
    if args.signal == SignalArg::Approach {
        anyhow::ensure!(
            !sets.iter().any(|s| s.eaten_max_pct.is_some()),
            "--signal approach: ключ `eaten=` у подхода не определён — размер на взводе и есть старт"
        );
        anyhow::ensure!(
            !sets.iter().any(FilterSet::uses_ret),
            "--signal approach: ключи ret* (ход монеты до сигнала) у подхода не определены — кэш подходов их не несёт; режим pool*/btc* по минуте взвода из --regime-from доступен"
        );
    }
    anyhow::ensure!(
        args.regime_from.is_some() || !sets.iter().any(FilterSet::uses_regime),
        "ключи pool*/btc* у наборов требуют --regime-from <study/regime>"
    );
    if let Some(dir) = &args.regime_from {
        anyhow::ensure!(dir.is_dir(), "--regime-from {}: не каталог", dir.display());
    }
    let need_regime = args.regime_from.is_some() && sets.iter().any(FilterSet::uses_regime);
    let need_ret = sets.iter().any(|s| s.ctx[..3].iter().any(|r| r.is_set()));
    let symbols = if args.symbols.is_empty() {
        pool_symbols(&args.root)?
    } else {
        args.symbols.clone()
    };
    Ok(GridPlan {
        queue_model,
        threads,
        deadlines,
        entry_ttls,
        band_exit_bps,
        entries,
        exits,
        forms,
        sets,
        need_regime,
        need_ret,
        symbols,
    })
}

/// Артефакты наборов: `<out-dir>[/<набор>]/{rounds,forms,manifest}` с шапками из плана.
pub(super) fn open_outputs(args: &BounceGridArgs, plan: &GridPlan) -> anyhow::Result<Vec<Outputs>> {
    let GridPlan {
        queue_model,
        threads,
        deadlines,
        entry_ttls,
        band_exit_bps,
        entries,
        exits,
        forms,
        sets,
        symbols,
        ..
    } = plan;
    // Ось выхода — канонично из разобранных форм (`ExitForm::label`), а не из
    // сырых флагов: пустой `--exit-form` печатался бы пустотой, а не `none`.
    let mut exit_forms = exits
        .iter()
        .map(ExitForm::label)
        .collect::<Vec<_>>()
        .join("+");
    // Ось «прилипания» (В-85) — в шапку только включённой: без флага шапка байт в байт прежняя.
    let mut earlies: Vec<String> = Vec::new();
    for f in forms.iter() {
        let l = f
            .early_exit_secs
            .map_or_else(|| "off".to_string(), |x| x.to_string());
        if !earlies.contains(&l) {
            earlies.push(l);
        }
    }
    if forms.iter().any(|f| f.early_exit_secs.is_some()) {
        exit_forms = format!("{exit_forms} early_exits={}", earlies.join("+"));
    }
    if args.carry_age {
        exit_forms = format!("{exit_forms} carry_age=on");
    }
    if args.touches_cache_only {
        exit_forms = format!("{exit_forms} touches_cache_only=on");
    }
    let header_for = |set: &FilterSet| {
        format!(
        "# lob bounce-grid: root={} days={} forms={} base=В-65(stop_form={:?} take_form={:?} take_floor_fees={:?} frontrun_only={} min_age_secs={:?} min_flow_pct={:?} side={} eaten_max={:?} usd_min={:?} ctx={} deadlines={:?}) RTT={}нс {} h3={:?} lot={} threads={} driver={} queue={} entry_post_only={} entry_ttl={} band_exit_bps={} signal={} entry_forms={} exit_forms={} paths=1:сделки-на-нашей-цене-частично(очередь) 2:сделка-в-сторону-от-нас-весь-остаток(приоритет-цены) 3:лучшая-цена-дошла-до-нашей-без-сделки-весь-остаток(оптимистично-по-размеру,-счётчик-n_fill_by_cross) touches={} verified={}{}",
        args.root.display(),
        if args.days.is_empty() {
            "all".to_string()
        } else {
            args.days.join("+")
        },
        forms.len(),
        args.stop_form,
        args.take_form,
        args.take_floor_fees,
        set.frontrun_only,
        set.min_age_secs,
        set.min_flow_pct,
        set.side.map_or("both", SideArg::label),
        set.eaten_max_pct,
        set.usd_min,
        set.ctx_label(),
        deadlines,
        args.median_rtt_ns,
        args.median_rtt_ns.provenance(),
        args.h3.h3_mode,
        match (args.order_qty_e9, args.order_usd) {
            (Some(v), _) => format!("e9:{v}x{}", args.order_qty_mult),
            (None, Some(usd)) => format!("usd:{usd}x{}", args.order_qty_mult),
            _ => format!("pool(22a)x{}", args.order_qty_mult),
        },
        threads,
        args.driver.label(),
        queue_model.label(),
        args.entry_post_only(),
        entry_ttls
            .iter()
            .map(|t| t.label())
            .collect::<Vec<_>>()
            .join("+"),
        band_exit_bps,
        // F6 (В-73): по какому сигналу вход (`touch` — гейт, `approach` —
        // запись подхода F1) и какими формами входа (ось `--entry-form`).
        args.signal.label(),
        entries
            .iter()
            .map(|e| e.label())
            .collect::<Vec<_>>()
            .join("+"),
        // F7/F8 (Б-75): форма выхода (ось `--exit-form`).
        exit_forms,
        args.touches_from
            .as_ref()
            .map_or("replay".to_string(), |d| format!("csv({})", d.display())),
        if args.allow_unverified {
            "allow-unverified(debug)"
        } else {
            "marker-required"
        },
        if set.name.is_empty() {
            String::new()
        } else {
            format!(" set={}", set.name)
        }
    )
    };
    let mut outs: Vec<Outputs> = Vec::with_capacity(sets.len());
    for set in sets.iter() {
        let dir = if set.name.is_empty() {
            args.out_dir.clone()
        } else {
            args.out_dir.join(&set.name)
        };
        let header = header_for(set);
        outs.push(Outputs::create(&dir, &header, args.carry_root.is_some())?);
        let mut m = std::fs::File::create(dir.join("manifest.txt"))?;
        writeln!(m, "{header}")?;
        writeln!(m, "symbols={}", symbols.join(","))?;
        writeln!(
            m,
            "forms={}",
            forms.iter().map(|f| f.label).collect::<Vec<_>>().join(",")
        )?;
    }
    if !args.sets.is_empty() {
        let mut m = std::fs::File::create(args.out_dir.join("manifest.txt"))?;
        writeln!(
            m,
            "# lob bounce-grid: sets={} (артефакты по подкаталогам)",
            sets.iter()
                .map(|s| s.name.as_str())
                .collect::<Vec<_>>()
                .join(",")
        )?;
        for spec in &args.sets {
            writeln!(m, "set={spec}")?;
        }
    }
    Ok(outs)
}
