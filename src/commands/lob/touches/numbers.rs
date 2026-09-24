//! Числа практиков (T42, вторая половина) — `--numbers`: квантили размера,
//! расстояния и времени жизни по касаниям и по умершим уровням, отдельно по
//! исходам и по осям В-44. Ни одного нового порога: корзины — существующие
//! (`touch_axes`, `size_bucket`), квантили — `stats::quantiles`. Вынесено из
//! `run_touches` в подмодуль (W5в).

use crate::commands::lob::profiles::size_bucket;
use crate::commands::lob::replay::ReplayStats;
use crate::commands::lob::{outcome_name, side_name};
use crate::lob::levels::TouchRecord;
use crate::lob::markout::{distance_bps_at_birth, mid_double_tick, sample_asof, MidSample};
use crate::lob::touch_axes::{
    age_bucket, frontrun_bucket, frontrun_share, round_bucket, touch_index_bucket,
};

use super::TouchesArgs;

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
    mids: &[MidSample],
    t: &TouchRecord,
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
pub(super) fn write_numbers(
    args: &TouchesArgs,
    replay: &ReplayStats,
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
                distance_before_1s_bps: distance_before_bps(
                    &day.mids,
                    t,
                    crate::lob::markout::APPROACH_MS[0],
                    tick,
                ),
                distance_before_10s_bps: distance_before_bps(
                    &day.mids,
                    t,
                    crate::lob::markout::APPROACH_MS[1],
                    tick,
                ),
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
            if let Some(b) = size_bucket(r.size_max as f64 / h3) {
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
