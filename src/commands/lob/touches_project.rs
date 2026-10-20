//! `lob touches-project` — вход §6 П-22 (TK-328): касания стен **спота** → цены в тиках **фьючерса**.
//! Берёт `touches-<SYM>.csv` суток спота и два набора бинлога (mid спота и фьючерса на `start_ms`), переписывает
//! в каждой строке `price_tick`, `frontrun_tick`, `stack_next_tick` через `lob::project::project_tick` (§3.2:
//! расстояние в bps сохраняется, округление в пассивную сторону — bid вниз, ask вверх) и пересчитывает
//! `round_zeros`. Остальные поля (лоты, силы, время, ходы) — как в строке, байт в байт. Исполнение — обычный
//! `bounce-grid --touches-from <out>` на бинлоге фьючерса.
//!
//! Mid — на конец последней полной секунды до `start_ms` (`Market::mid_before`, без заглядывания вперёд). Строка,
//! у которой нет mid одного из рынков или проекция не легла в `i64`, отбрасывается и считается в отчёте (не
//! подменяется). Гейт «байт в байт»: спот и фьючерс — один и тот же набор бинлога ⇒ множитель ровно 1,0 ⇒ выходной
//! CSV равен входному (`project_tick` не округляет при равных mid и шаге).

use std::path::PathBuf;

use clap::Args;

use crate::book::Side;
use crate::lob::levels::round_zeros;
use crate::lob::project::project_tick;

use super::sync_basis::{forward_fill, read_market};

/// Аргументы `lob touches-project`.
#[derive(Debug, Args)]
pub struct TouchesProjectArgs {
    /// `touches-<SYM>.csv` суток спота (вход).
    #[arg(long = "in")]
    pub input: PathBuf,
    /// Куда писать CSV касаний в тиках фьючерса (`<каталог>/<сутки>/touches-<SYM>.csv` — раскладка `--touches-from`).
    #[arg(long)]
    pub out: PathBuf,
    /// Части суток спота (`*.binlog` / `*.binlog.zst`), по порядку.
    #[arg(long, num_args = 1.., required = true)]
    pub spot: Vec<PathBuf>,
    /// Части суток фьючерса, по порядку.
    #[arg(long, num_args = 1.., required = true)]
    pub fut: Vec<PathBuf>,
}

/// Индекс колонки по имени.
fn col(header: &csv::StringRecord, name: &str) -> anyhow::Result<usize> {
    header
        .iter()
        .position(|h| h == name)
        .ok_or_else(|| anyhow::anyhow!("нет колонки {name}"))
}

/// Проекция одного поля-тика: пусто остаётся пустым; число, не изменившееся, пишется исходной строкой.
fn project_field(
    raw: &str,
    side: Side,
    mids: (f64, f64),
    ticks: (i64, i64),
) -> anyhow::Result<Option<String>> {
    if raw.is_empty() {
        return Ok(Some(String::new()));
    }
    let tick: i64 = raw.parse().map_err(|e| anyhow::anyhow!("{raw:?}: {e}"))?;
    Ok(
        project_tick(tick, side, ticks.0, ticks.1, mids.0, mids.1).map(|t| {
            if t == tick {
                raw.to_owned()
            } else {
                t.to_string()
            }
        }),
    )
}

/// Прогнать файл; строка отчёта: `строк,прошло,без_mid_или_вне_i64`.
pub fn run_touches_project(args: &TouchesProjectArgs) -> anyhow::Result<String> {
    let spot = read_market(&args.spot)?;
    let fut = read_market(&args.fut)?;
    let (s_fill, f_fill) = (forward_fill(&spot.mid), forward_fill(&fut.mid));
    let ticks = (spot.tick_e9, fut.tick_e9);

    let mut rd = csv::Reader::from_path(&args.input)?;
    let header = rd.headers()?.clone();
    let c_side = col(&header, "side")?;
    let c_start = col(&header, "start_ms")?;
    let c_price = col(&header, "price_tick")?;
    let c_zeros = col(&header, "round_zeros")?;
    let c_front = col(&header, "frontrun_tick")?;
    let c_next = col(&header, "stack_next_tick")?;
    if let Some(dir) = args.out.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut w = csv::Writer::from_path(&args.out)?;
    w.write_record(&header)?;
    let (mut rows, mut kept) = (0u64, 0u64);
    for rec in rd.records() {
        let rec = rec?;
        rows += 1;
        let side = match &rec[c_side] {
            "bid" => Side::Bid,
            "ask" => Side::Ask,
            other => anyhow::bail!("side {other:?}"),
        };
        let start: i64 = rec[c_start].parse()?;
        let (Some(s_mid), Some(f_mid)) = (
            spot.mid_before(&s_fill, start),
            fut.mid_before(&f_fill, start),
        ) else {
            continue;
        };
        let mids = (s_mid, f_mid);
        let (Some(price), Some(front), Some(next)) = (
            project_field(&rec[c_price], side, mids, ticks)?,
            project_field(&rec[c_front], side, mids, ticks)?,
            project_field(&rec[c_next], side, mids, ticks)?,
        ) else {
            continue;
        };
        let zeros = if price == rec[c_price] {
            rec[c_zeros].to_owned()
        } else {
            round_zeros(price.parse()?).to_string()
        };
        let out: Vec<&str> = rec
            .iter()
            .enumerate()
            .map(|(i, v)| match i {
                _ if i == c_price => price.as_str(),
                _ if i == c_front => front.as_str(),
                _ if i == c_next => next.as_str(),
                _ if i == c_zeros => zeros.as_str(),
                _ => v,
            })
            .collect();
        w.write_record(&out)?;
        kept += 1;
    }
    w.flush()?;
    Ok(format!("rows,{rows}\nkept,{kept}\ndropped,{}", rows - kept))
}

#[cfg(test)]
mod tests;
