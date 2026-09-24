//! Переезды плотностей (T42, В-46) — `--moves`: пары «смерть → рождение»
//! склеенных суток, CSV и печать сводки. Вынесено из `run_touches` в
//! подмодуль (W5в); проверка `--moves-window-ms` — заранее, `plan::resolve`
//! (W5а), здесь она уже гарантированно задана и положительна.

use crate::commands::lob::replay::ReplayDay;
use crate::commands::lob::{outcome_name, side_name, some_or_empty};
use crate::lob::moves::{by_dt_bins, find_pairs, histogram, quantiles};

/// Переезды плотностей за все сутки прогона: сутки склеиваются в один поток
/// (пара через полночь UTC — такая же пара, как внутри суток, критерий
/// приёмки T42), пишутся в `path`, сводка — в stdout.
pub(super) fn run_moves(
    days: &[ReplayDay],
    window_ms: i64,
    bin_ms: Option<f64>,
    symbol: &str,
    path: &std::path::Path,
) -> anyhow::Result<()> {
    let mut records = Vec::new();
    let mut touches_all = Vec::new();
    let mut mids = Vec::new();
    for day in days {
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
        symbol,
        pairs.len(),
        window_ms,
        100.0 * pairs.iter().filter(|p| p.dp_ticks == 0).count() as f64 / pairs.len().max(1) as f64,
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
    if let Some(width) = bin_ms {
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
    Ok(())
}
