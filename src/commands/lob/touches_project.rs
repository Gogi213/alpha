//! `lob touches-project` — вход §6 П-22 (TK-328): касания стен **спота** → цены в тиках **фьючерса**.
//! Берёт `touches-<SYM>.csv` суток спота и два набора бинлога (mid спота и фьючерса на `start_ms`), переписывает
//! в каждой строке `price_tick`, `frontrun_tick`, `stack_next_tick` через `lob::project::project_tick` (§3.2:
//! расстояние в bps сохраняется, округление в пассивную сторону — bid вниз, ask вверх) и пересчитывает
//! `round_zeros`. Колонки в **лотах** (`LOT_COLUMNS`) пересчитываются из лота спота в лот фьючерса через количество
//! (`lots·step_спот/step_фьюч`, до ближайшего лота): `usd_min`/`holds_at_size` считают номинал как `тик·лот` фьючерса,
//! а лоты спота и фьючерса (ADA: 0,01 и 1) различаются. Остальные поля (силы в %, время, ходы) — как в строке, байт в
//! байт. Исполнение — обычный `bounce-grid --touches-from <out>` на бинлоге фьючерса.
//!
//! Mid — на конец последней полной секунды до `start_ms` (`Market::mid_before`, без заглядывания вперёд). Строка,
//! у которой нет mid одного из рынков или проекция не легла в `i64`, отбрасывается и считается в отчёте (не
//! подменяется). Гейт «байт в байт»: спот и фьючерс — один и тот же набор бинлога ⇒ множитель ровно 1,0, шаги
//! лота равны ⇒ выходной CSV равен входному (`project_tick` не округляет при равных mid и шаге, лоты не трогаются).

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

/// Колонки `touches-<SYM>.csv` в лотах (размер, оборот, глубина): единицы лота спота → лота фьючерса.
const LOT_COLUMNS: [&str; 9] = [
    "size_at_touch",
    "size_max_before",
    "traded_during",
    "frontrun_lots",
    "swept_lots",
    "traded_1s",
    "traded_2s",
    "traded_3s",
    "flow_1h_lots",
];
/// Колонка лотов, которой может не быть в старом кэше (T2): конвертируется, если есть.
const OPTIONAL_LOT_COLUMN: &str = "depth_behind_lots";

/// Лоты спота → лоты фьючерса по одному и тому же количеству базовой монеты: `lots·step_s/step_f`, до ближайшего
/// целого (половина вверх). Равные шаги — строка без изменений (байт в байт). Пусто остаётся пустым.
fn convert_lots(raw: &str, steps: (i64, i64)) -> anyhow::Result<String> {
    if raw.is_empty() || steps.0 == steps.1 {
        return Ok(raw.to_owned());
    }
    anyhow::ensure!(steps.0 > 0 && steps.1 > 0, "шаг лота ≤ 0: {steps:?}");
    let lots: i64 = raw.parse().map_err(|e| anyhow::anyhow!("{raw:?}: {e}"))?;
    let (num, den) = (i128::from(lots) * i128::from(steps.0), i128::from(steps.1));
    let r = (2 * num + den).div_euclid(2 * den);
    i64::try_from(r)
        .map(|v| v.to_string())
        .map_err(|_| anyhow::anyhow!("лоты {lots} не легли в i64 при шагах {steps:?}"))
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
    let steps = (spot.step_e9, fut.step_e9);

    let mut rd = csv::Reader::from_path(&args.input)?;
    let header = rd.headers()?.clone();
    let c_side = col(&header, "side")?;
    let c_start = col(&header, "start_ms")?;
    let c_price = col(&header, "price_tick")?;
    let c_zeros = col(&header, "round_zeros")?;
    let c_front = col(&header, "frontrun_tick")?;
    let c_next = col(&header, "stack_next_tick")?;
    let mut c_lots: Vec<usize> = Vec::with_capacity(LOT_COLUMNS.len() + 1);
    for name in LOT_COLUMNS {
        c_lots.push(col(&header, name)?);
    }
    c_lots.extend(header.iter().position(|h| h == OPTIONAL_LOT_COLUMN));
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
        let lots: Vec<String> = c_lots
            .iter()
            .map(|&i| convert_lots(&rec[i], steps))
            .collect::<anyhow::Result<_>>()?;
        let out: Vec<&str> = rec
            .iter()
            .enumerate()
            .map(|(i, v)| match i {
                _ if i == c_price => price.as_str(),
                _ if i == c_front => front.as_str(),
                _ if i == c_next => next.as_str(),
                _ if i == c_zeros => zeros.as_str(),
                _ => c_lots
                    .iter()
                    .position(|&c| c == i)
                    .map_or(v, |k| lots[k].as_str()),
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
