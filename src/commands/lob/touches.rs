//! `lob touches` — CLI-обёртка касаний живых уровней (таск 35, В-42).
//! Сам учёт касаний — `crate::lob::levels` (`TouchRecord`,
//! `LevelTracker::observe_frame_with_touches`), markout и подход —
//! `crate::lob::markout` (`markouts_for_touch`, `approaches_for_touch`);
//! реплей суточных файлов — общий с `levels`/`markout` `super::replay_symbol`
//! (`ReplayDay.touches`). Здесь только флаги, CSV и код выхода.
//!
//! Колонки: запись касания как есть (`birth_ms` — рождение уровня, как в
//! `levels-*.csv`) плюс `age_ms` (возраст уровня на момент касания),
//! `dist_bps` (расстояние уровня до середины при его рождении — один расчёт с
//! осью `distance` у `profiles`, `markout::distance_bps_at_birth`), markout на
//! четырёх горизонтах `HORIZONS_MS` от среза как есть на `start_ms` (В-43) со
//! знаком «в сторону отскока», подход за `APPROACH_MS` (1 с, 10 с) со знаком
//! «к уровню». Пустая ячейка — нет данных (нет среза на момент касания или
//! на горизонте), не ноль. `frontrun_lots` — за секунду до касания,
//! `swept_lots` — на последнем кадре до него (В-45); `within_touch_<h>` —
//! горизонт не длиннее касания (`markout::within_touch`): `m_<h>` там ≈ 0
//! по построению, читателю средних такую ячейку надо пропустить.

use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::LevelsConfig;
use crate::lob::markout::{
    approaches_for_touch, distance_bps_at_birth, long_markouts_for_touch, markouts_for_touch,
    mid_double_tick, sample_asof, within_touch, APPROACH_MS, HORIZONS_MS,
};

use super::{
    outcome_name, replay_symbol, resolve_h3_mode_with_k, side_name, some_or_empty, H3Args,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::lob::moves::{by_dt_bins, find_pairs, histogram, quantiles};
use crate::lob::touch_axes::{
    age_bucket, frontrun_bucket, frontrun_share, round_bucket, touch_index_bucket,
};

/// Аргументы `lob touches`: читает суточные файлы, пишет касания живых
/// уровней с признаками практиков. Режим `H3` — без умолчания, как у
/// `levels`; `--h3-k` — тот же пересчёт пола на лету (таск 18).
#[derive(Debug, Args)]
pub struct TouchesArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд).
    #[command(flatten)]
    pub h3: H3Args,
    /// Множитель относительного порога `floor` (таск 18, В-30/D05):
    /// `h3_lots = floor(k × median_trade_lots)` из `instruments.csv`.
    /// Только `--h3-mode floor`.
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Прогрев в мс: только режим `percentile`; `floor` не читает.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс (нужно трекеру, в касания не пишется).
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать касания (по умолчанию `<root>/touches-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Заодно измерить **переезды плотностей** (T42, В-46) и записать пары
    /// «смерть → рождение» в этот CSV. Метка `moved` не ставится: измеряется
    /// распределение, границы назначаются отдельным решением.
    #[arg(long)]
    pub moves: Option<PathBuf>,
    /// Окно поиска рождения для `--moves`, мс. Без умолчания: это параметр
    /// измерения, а не константа кода (В-46), и он печатается в шапке.
    #[arg(long)]
    pub moves_window_ms: Option<i64>,
    /// Шаг гистограммы `dt_ms` для `--moves`, мс (без него гистограмма не
    /// печатается: шаг — параметр, а не назначенное число).
    #[arg(long)]
    pub moves_bin_ms: Option<f64>,
    /// Выгрузить **числа практиков** (T42): квантили p10/p50/p90 размера (лоты
    /// / ×H3 / $), расстояния (bps) и времени жизни — по касаниям и по умершим
    /// уровням, отдельно по исходам и по осям В-44.
    #[arg(long)]
    pub numbers: Option<PathBuf>,
}

/// Итог `lob touches` для печати диспетчером.
#[derive(Debug)]
pub struct TouchesSummary {
    pub days: usize,
    pub touches: usize,
    pub out: PathBuf,
}

/// Ширина строки CSV — один источник арности для заголовка и строки:
/// расхождение не компилируется.
const TOUCHES_WIDTH: usize = 32;

/// Заголовок CSV: запись касания как есть, затем производные. `birth_ms` —
/// как в `levels-*.csv`/`markout-*.csv`, для джойна по (сторона, тик,
/// рождение).
pub(crate) const TOUCHES_COLUMNS: [&str; TOUCHES_WIDTH] = [
    "day_utc",
    "side",
    "price_tick",
    "touch_index",
    "start_ms",
    "end_ms",
    "duration_ms",
    "birth_ms",
    "age_ms",
    "size_at_touch",
    "size_max_before",
    "traded_during",
    "frontrun_lots",
    "frontrun_tick",
    "swept_lots",
    "round_zeros",
    "ended_by_death",
    "stack_levels",
    "dist_bps",
    "m_100ms",
    "m_1s",
    "m_10s",
    "m_60s",
    "within_touch_100ms",
    "within_touch_1s",
    "within_touch_10s",
    "within_touch_60s",
    "approach_1s",
    "approach_10s",
    // Длинные горизонты (B3, В-58 п. 4): 10 мин / 1 ч / 2 ч — «от секунд до,
    // наверно, пары часов» (ответ владельца 2). Отдельные колонки, а не
    // продолжение `m_*`: у тех есть парные `within_touch_*`, у этих — нет.
    "m_10m",
    "m_1h",
    "m_2h",
];

/// Реплей символа тем же `replay_symbol`, что `levels`/`markout`, и запись
/// касаний в CSV. Порядок строк — порядок выдачи трекера внутри суток.
pub fn run_touches(args: &TouchesArgs) -> anyhow::Result<TouchesSummary> {
    debug_assert_eq!(
        HORIZONS_MS,
        [100, 1_000, 10_000, 60_000],
        "порядок колонок m_* обязан совпадать с горизонтами"
    );
    debug_assert_eq!(
        APPROACH_MS,
        [1_000, 10_000],
        "порядок колонок approach_* обязан совпадать с окнами подхода"
    );
    let mode = resolve_h3_mode_with_k(
        &args.root,
        &args.symbol,
        args.h3.h3_mode,
        args.h3.h3_lots,
        args.h3_k,
    )?;
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    // Порог в лотах — для чисел практиков (×H3) и шапок артефактов.
    let h3_lots = match mode {
        crate::lob::levels::H3Mode::Floor { h3_lots }
        | crate::lob::levels::H3Mode::Percentile { h3_lots } => h3_lots,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("touches-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(TOUCHES_COLUMNS)?;
    let mut n = 0usize;
    for day in &replay.days {
        for t in &day.touches {
            let ms = markouts_for_touch(t, &day.mids);
            let inside = within_touch(t.duration_ms);
            let ap = approaches_for_touch(t, &day.mids);
            let long = long_markouts_for_touch(t, &day.mids);
            let row: [String; TOUCHES_WIDTH] = [
                day.day.clone(),
                side_name(t.side).to_string(),
                t.price_tick.to_string(),
                t.touch_index.to_string(),
                t.start_ms.to_string(),
                t.end_ms.to_string(),
                t.duration_ms.to_string(),
                t.level_birth_ms.to_string(),
                t.age_ms().to_string(),
                t.size_at_touch.to_string(),
                t.size_max_before.to_string(),
                t.traded_during.to_string(),
                t.frontrun_lots.to_string(),
                t.frontrun_tick.map_or(String::new(), |v| v.to_string()),
                t.swept_lots.to_string(),
                t.round_zeros.to_string(),
                t.ended_by_death.to_string(),
                t.stack_levels.to_string(),
                some_or_empty(distance_bps_at_birth(
                    &day.mids,
                    t.level_birth_ms,
                    t.price_tick,
                )),
                some_or_empty(ms[0]),
                some_or_empty(ms[1]),
                some_or_empty(ms[2]),
                some_or_empty(ms[3]),
                inside[0].to_string(),
                inside[1].to_string(),
                inside[2].to_string(),
                inside[3].to_string(),
                some_or_empty(ap[0]),
                some_or_empty(ap[1]),
                some_or_empty(long[0]),
                some_or_empty(long[1]),
                some_or_empty(long[2]),
            ];
            w.write_record(row)?;
            n += 1;
        }
    }
    w.flush()?;

    // Переезды плотностей (T42, В-46) — отдельным файлом: окно поиска
    // приходит флагом и печатается, метка `moved` не ставится.
    if let Some(path) = &args.moves {
        let Some(window_ms) = args.moves_window_ms else {
            anyhow::bail!("--moves требует --moves-window-ms: окно поиска — параметр измерения, не константа (В-46)");
        };
        anyhow::ensure!(window_ms > 0, "--moves-window-ms обязан быть положителен");
        // Сутки склеиваются в один поток: пара через полночь UTC — такая же
        // пара, как внутри суток (критерий приёмки T42).
        let mut records = Vec::new();
        let mut touches_all = Vec::new();
        let mut mids = Vec::new();
        for day in &replay.days {
            records.extend(day.records.iter().copied());
            touches_all.extend(day.touches.iter().copied());
            mids.extend(day.mids.iter().copied());
        }
        mids.sort_by_key(|s| s.ts_ms);
        let pairs = find_pairs(&records, &touches_all, &mids, window_ms);
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let mut mw = csv::Writer::from_path(path)?;
        mw.write_record([
            "side",
            "dt_ms",
            "dp_ticks",
            "size_ratio",
            "zeros_from",
            "zeros_to",
            "mid_10s_bps",
            "mid_60s_bps",
            "touched",
            "outcome_to",
        ])?;
        for p in &pairs {
            mw.write_record([
                side_name(p.side).to_string(),
                p.dt_ms.to_string(),
                p.dp_ticks.to_string(),
                some_or_empty(p.size_ratio),
                p.zeros_from.to_string(),
                p.zeros_to.to_string(),
                some_or_empty(p.mid_10s_bps),
                some_or_empty(p.mid_60s_bps),
                p.touched.to_string(),
                p.outcome_to.map(outcome_name).unwrap_or("").to_string(),
            ])?;
        }
        mw.flush()?;
        println!(
            "moves: {} {} пар за {} мс окна (сняли → родилось; рядом {:.1} %), ноль-Δp {:.1} %, коснуты {:.1} % → {}",
            args.symbol,
            pairs.len(),
            window_ms,
            100.0 * pairs.iter().filter(|p| p.dp_ticks == 0).count() as f64
                / pairs.len().max(1) as f64,
            100.0
                * pairs
                    .iter()
                    .filter(|p| p.dp_ticks.abs() <= 1)
                    .count() as f64
                / pairs.len().max(1) as f64,
            100.0 * pairs.iter().filter(|p| p.touched).count() as f64 / pairs.len().max(1) as f64,
            path.display()
        );
        let dts: Vec<f64> = pairs.iter().map(|p| p.dt_ms as f64).collect();
        let dps: Vec<f64> = pairs
            .iter()
            .map(|p| p.dp_ticks.unsigned_abs() as f64)
            .collect();
        let ratios: Vec<f64> = pairs.iter().filter_map(|p| p.size_ratio).collect();
        if let Some((a, b, c)) = quantiles(&dts) {
            println!("moves: Δt, мс — p10 {a:.0} · p50 {b:.0} · p90 {c:.0}");
        }
        if let Some((a, b, c)) = quantiles(&dps) {
            println!("moves: |Δp|, тиков — p10 {a:.0} · p50 {b:.0} · p90 {c:.0}");
        }
        if let Some((a, b, c)) = quantiles(&ratios) {
            println!("moves: размер новый/старый — p10 {a:.2} · p50 {b:.2} · p90 {c:.2}");
        }
        if let Some(width) = args.moves_bin_ms {
            let h = histogram(&dts, width);
            let text: Vec<String> = h.iter().map(|(x, n)| format!("{x:.0}мс:{n}")).collect();
            println!(
                "moves: Δt гистограмма шагом {width:.0} мс — {}",
                text.join(" ")
            );
            // Совместный свод: маргиналы вырождены первым бакетом, поэтому
            // вопрос «есть ли кластер настоящих переездов» решается только
            // сравнением корзин Δt по остальным признакам (В-46).
            println!("moves: корзина Δt · пар · медиана |Δp| · медиана размера · Δp=0 · коснуты");
            for b in by_dt_bins(&pairs, width as i64) {
                let m = |v: Option<f64>, d: usize| match v {
                    Some(x) => format!("{x:.d$}"),
                    None => "—".to_string(),
                };
                println!(
                    "moves:   от {:.0} мс · {} · {} тиков · {} · {:.1} % · {:.1} %",
                    b.dt_from_ms,
                    b.n,
                    m(b.dp_abs_median, 0),
                    m(b.ratio_median, 2),
                    100.0 * b.zero_dp_share,
                    100.0 * b.touched_share
                );
            }
        }
    }

    if args.numbers.is_some() {
        write_numbers(args, &replay, h3_lots, replay.tick_e9, replay.step_e9)?;
    }

    Ok(TouchesSummary {
        days: replay.days.len(),
        touches: n,
        out,
    })
}

// ---------------------------------------------------------------------------
// Числа практиков (T42, вторая половина): квантили размера, расстояния и
// времени жизни по касаниям и по умершим уровням, отдельно по исходам и по
// осям В-44. Ни одного нового порога: корзины — существующие (`touch_axes`,
// `SIZE_LABELS`), квантили — `stats::quantiles`.
// ---------------------------------------------------------------------------

/// Одно наблюдение для квантилей: размер тремя способами, расстояние и жизнь.
#[derive(Debug, Clone, Copy)]
struct NumSample {
    size_lots: f64,
    size_x_h3: f64,
    size_usd: f64,
    distance_bps: Option<f64>,
    /// Расстояние по формулировке практиков: от середины за 1 с (10 с) **до**
    /// касания до цены уровня, bps по модулю. Их «от 0.2 до 0.7 %» — это оно,
    /// а не расстояние в момент рождения уровня (окна `APPROACH_MS` уже есть).
    distance_before_1s_bps: Option<f64>,
    distance_before_10s_bps: Option<f64>,
    lifetime_s: f64,
}

fn push_sample(
    groups: &mut std::collections::BTreeMap<String, Vec<NumSample>>,
    key: &str,
    s: NumSample,
) {
    groups.entry(key.to_string()).or_default().push(s);
}

/// Расстояние от середины за `back_ms` **до** касания до цены уровня, bps по
/// модулю — величина практиков («от 0.2 до 0.7 %»). Считается на существующих
/// окнах `APPROACH_MS`: новых чисел не заводится.
fn distance_before_bps(
    mids: &[crate::lob::markout::MidSample],
    t: &crate::lob::levels::TouchRecord,
    back_ms: i64,
    tick: f64,
) -> Option<f64> {
    let s = sample_asof(mids, t.start_ms, -back_ms)?;
    let mid_px = mid_double_tick(s.bid_tick, s.ask_tick) as f64 / 2.0 * tick;
    if mid_px <= 0.0 {
        return None;
    }
    Some(((t.price_tick as f64 * tick) - mid_px).abs() / mid_px * 10_000.0)
}

/// Пишет строки квантилей: одна строка на (охват, группа).
fn write_number_rows(
    w: &mut csv::Writer<std::fs::File>,
    scope: &str,
    groups: &std::collections::BTreeMap<String, Vec<NumSample>>,
) -> anyhow::Result<()> {
    let num = |v: Option<f64>, d: usize| match v {
        Some(x) => format!("{x:.d$}"),
        None => "—".to_string(),
    };
    for (group, samples) in groups {
        let col = |f: &dyn Fn(&NumSample) -> Option<f64>| -> Vec<f64> {
            samples.iter().filter_map(f).collect()
        };
        let lots = col(&|s: &NumSample| Some(s.size_lots));
        let xh3 = col(&|s: &NumSample| Some(s.size_x_h3));
        let usd = col(&|s: &NumSample| Some(s.size_usd));
        let dist = col(&|s: &NumSample| s.distance_bps);
        let dist_before_1s = col(&|s: &NumSample| s.distance_before_1s_bps);
        let dist_before_10s = col(&|s: &NumSample| s.distance_before_10s_bps);
        let life = col(&|s: &NumSample| Some(s.lifetime_s));
        let t = crate::stats::quantiles;
        let (l1, l2, l3) = t(&lots).unwrap_or((f64::NAN, f64::NAN, f64::NAN));
        let (x1, x2, x3) = t(&xh3).unwrap_or((f64::NAN, f64::NAN, f64::NAN));
        let (u1, u2, u3) = t(&usd).unwrap_or((f64::NAN, f64::NAN, f64::NAN));
        let (d1, d2, d3) = match t(&dist) {
            Some(v) => (Some(v.0), Some(v.1), Some(v.2)),
            None => (None, None, None),
        };
        let (e1a, e1b, e1c) = match t(&dist_before_1s) {
            Some(v) => (Some(v.0), Some(v.1), Some(v.2)),
            None => (None, None, None),
        };
        let (e10a, e10b, e10c) = match t(&dist_before_10s) {
            Some(v) => (Some(v.0), Some(v.1), Some(v.2)),
            None => (None, None, None),
        };
        let (f1, f2, f3) = t(&life).unwrap_or((f64::NAN, f64::NAN, f64::NAN));
        w.write_record([
            scope.to_string(),
            group.clone(),
            samples.len().to_string(),
            num(Some(l1), 1),
            num(Some(l2), 1),
            num(Some(l3), 1),
            num(Some(x1), 2),
            num(Some(x2), 2),
            num(Some(x3), 2),
            num(Some(u1), 0),
            num(Some(u2), 0),
            num(Some(u3), 0),
            num(d1, 2),
            num(d2, 2),
            num(d3, 2),
            num(e1a, 2),
            num(e1b, 2),
            num(e1c, 2),
            num(e10a, 2),
            num(e10b, 2),
            num(e10c, 2),
            num(Some(f1), 2),
            num(Some(f2), 2),
            num(Some(f3), 2),
        ])?;
    }
    Ok(())
}

/// Числа практиков за прогон: `--numbers`.
fn write_numbers(
    args: &TouchesArgs,
    replay: &super::replay::ReplayStats,
    h3_lots: i64,
    tick_e9: i64,
    step_e9: i64,
) -> anyhow::Result<std::path::PathBuf> {
    let path = args.numbers.clone().expect("вызывается только с --numbers");
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let tick = tick_e9 as f64 / 1e9;
    let lot_qty = step_e9 as f64 / 1e9;
    let h3 = h3_lots.max(1) as f64;
    let mut touch_groups: std::collections::BTreeMap<String, Vec<NumSample>> = Default::default();
    let mut death_groups: std::collections::BTreeMap<String, Vec<NumSample>> = Default::default();

    for day in &replay.days {
        for t in &day.touches {
            let s = NumSample {
                size_lots: t.size_at_touch as f64,
                size_x_h3: t.size_at_touch as f64 / h3,
                size_usd: t.size_at_touch as f64 * lot_qty * (t.price_tick as f64 * tick),
                distance_bps: distance_bps_at_birth(&day.mids, t.level_birth_ms, t.price_tick),
                distance_before_1s_bps: distance_before_bps(&day.mids, t, APPROACH_MS[0], tick),
                distance_before_10s_bps: distance_before_bps(&day.mids, t, APPROACH_MS[1], tick),
                lifetime_s: t.duration_ms as f64 / 1000.0,
            };
            push_sample(&mut touch_groups, "все", s);
            push_sample(
                &mut touch_groups,
                if t.ended_by_death {
                    "исход:проели"
                } else {
                    "исход:отскочила"
                },
                s,
            );
            push_sample(
                &mut touch_groups,
                &format!("сторона:{}", side_name(t.side)),
                s,
            );
            if let Some(b) = age_bucket(t.age_ms()) {
                push_sample(&mut touch_groups, &format!("возраст:{b}"), s);
            }
            push_sample(
                &mut touch_groups,
                &format!("номер:{}", touch_index_bucket(t.touch_index)),
                s,
            );
            push_sample(
                &mut touch_groups,
                &format!("круглость:{}", round_bucket(t.round_zeros)),
                s,
            );
            if let Some(share) = frontrun_share(t.frontrun_lots, t.size_at_touch) {
                if let Some(b) = frontrun_bucket(share) {
                    push_sample(&mut touch_groups, &format!("фронтран:{b}"), s);
                }
            }
        }
        for r in &day.records {
            let s = NumSample {
                size_lots: r.size_max as f64,
                size_x_h3: r.size_max as f64 / h3,
                size_usd: r.size_max as f64 * lot_qty * (r.price_tick as f64 * tick),
                distance_bps: distance_bps_at_birth(&day.mids, r.birth_ms, r.price_tick),
                distance_before_1s_bps: None,
                distance_before_10s_bps: None,
                lifetime_s: r.lifetime_ms as f64 / 1000.0,
            };
            push_sample(&mut death_groups, "все", s);
            push_sample(
                &mut death_groups,
                &format!("исход:{}", outcome_name(r.outcome())),
                s,
            );
            push_sample(
                &mut death_groups,
                &format!("сторона:{}", side_name(r.side)),
                s,
            );
            if let Some(b) = super::profiles::size_bucket(r.size_max as f64 / h3) {
                push_sample(&mut death_groups, &format!("размер:{b}"), s);
            }
        }
    }

    let mut file = std::fs::File::create(&path)?;
    use std::io::Write as _;
    writeln!(
        file,
        "# числа практиков: {} {} порог H3={} лотов, шаг цены {tick}, шаг лота {lot_qty}; квантили p10/p50/p90 (stats::quantiles, тип 7); расстояние — от середины в момент рождения уровня (T35), не от текущей цены",
        args.symbol,
        args.root.display(),
        h3_lots
    )?;
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "scope",
        "group",
        "n",
        "size_lots_p10",
        "size_lots_p50",
        "size_lots_p90",
        "size_x_h3_p10",
        "size_x_h3_p50",
        "size_x_h3_p90",
        "size_usd_p10",
        "size_usd_p50",
        "size_usd_p90",
        "distance_bps_p10",
        "distance_bps_p50",
        "distance_bps_p90",
        "distance_before_1s_bps_p10",
        "distance_before_1s_bps_p50",
        "distance_before_1s_bps_p90",
        "distance_before_10s_bps_p10",
        "distance_before_10s_bps_p50",
        "distance_before_10s_bps_p90",
        "lifetime_s_p10",
        "lifetime_s_p50",
        "lifetime_s_p90",
    ])?;
    write_number_rows(&mut w, "касания", &touch_groups)?;
    write_number_rows(&mut w, "смерти", &death_groups)?;
    w.flush()?;
    println!(
        "numbers: {} групп касаний и {} групп смертей → {}",
        touch_groups.len(),
        death_groups.len(),
        path.display()
    );
    Ok(path)
}

#[cfg(test)]
mod tests;
