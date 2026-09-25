//! `lob trades` — экспорт ленты сделок (T1, П-02: открывает Г-46 — знак CVD
//! до подхода, и точную версию Г-36 — айсберг). Источник событий — та же
//! проверка, что уже отделяет сделки от книжных дельт при файловом реплее
//! (`is_trade_ev`, `bybit::verify.rs`; ей же кормятся `FileReplayer::push_frame`
//! для `TradePoint` и `replay.rs::trade_hit_from_record` для `TradeHit`
//! трекера уровней) — здесь та же проверка `Record.ev`, но вместо потребления
//! трекером или сборки книги — сырая строка в CSV. Книга здесь не нужна
//! вовсе: сделка в бинлоге — самостоятельная запись, не дельта, которую
//! нужно применить к книге, поэтому в отличие от `touches`/`levels` нет ни
//! `Book`, ни `FileReplayer`, ни `LevelTracker` — только `Reader` по кадрам
//! файла.
//!
//! Колонки (сырой дамп, без агрегации — CVD и окна считает python поверх
//! этого CSV, тикет T3 `tools/compute/wall-events.py`): `day_utc` (сутки
//! файла, как у `touches`), `exch_ts_ms`/`local_ts_ms` (метки биржи и
//! получения, `Record.exch_ts_ns`/`local_ts_ns`), `side` (`buy`/`sell` —
//! сторона агрессора, не сторона книги: покупатель ест аск), `price_tick`/
//! `price_usd` (одно и то же число, тик и целочисленный $, А1 — без `f64`),
//! `qty_lots`, `block`, `rpi` (те же флаги записи, что видит трекер уровней
//! в `lob::levels::TradeHit`: блочные сделки не едят видимую ликвидность,
//! RPI — исполнение об невидимую заявку).
//!
//! Части суток (`-pN`, таск 22) читаются подряд как один поток — тот же
//! резолвер, что у остальных читателей (`session_binlog_for`); К1 — маркер
//! сверки `verify-<SYMBOL>.status == ok`, как у `touches`
//! (`--allow-unverified` снимает).

use std::path::PathBuf;

use clap::Args;
use hftbacktest::types::LOCAL_BUY_TRADE_EVENT;

use crate::binlog::Reader;
use crate::bybit::verify::is_trade_ev;

use super::parts::read_frame_soft;

mod plan;
mod row;

#[cfg(test)]
mod tests;

/// Аргументы `lob trades`: интерфейс — как у `lob touches`
/// (`--root`/`--symbol`/`--out`), плюс повторяемый `--day` (пусто — все
/// сутки записи, как читают многосуточные команды, например `fill-capacity`).
#[derive(Debug, Args)]
pub struct TradesArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Сутки UTC `YYYY-MM-DD` (повторяемый флаг); пусто — все сутки записи.
    #[arg(long = "day")]
    pub days: Vec<String>,
    /// Куда писать ленту (по умолчанию `<root>/trades-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Снять требование маркера сверки `verify-<SYMBOL>.status == ok`
    /// (отладочные данные; К1 аудита 18.09 — тот же гейт, что у `touches`).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
}

/// Итог `lob trades` для печати диспетчером.
#[derive(Debug)]
pub struct TradesSummary {
    pub days: usize,
    pub trades: usize,
    /// Сделок с покупателем-агрессором (`side = buy`) среди `trades`.
    pub buy: usize,
    /// Сделок с продавцом-агрессором (`side = sell`) среди `trades`.
    pub sell: usize,
    pub out: PathBuf,
}

/// Ширина строки CSV — один источник арности для заголовка и строки, как у
/// `touches::TOUCHES_WIDTH`: расхождение не компилируется (см. `row::trade_row`).
const TRADES_WIDTH: usize = 9;

pub(crate) const TRADES_COLUMNS: [&str; TRADES_WIDTH] = [
    "day_utc",
    "exch_ts_ms",
    "local_ts_ms",
    "side",
    "price_tick",
    "price_usd",
    "qty_lots",
    "block",
    "rpi",
];

/// Читает бинлоги символа (`session_binlog_for`, части суток подряд, как
/// один поток) и пишет `trades-<SYMBOL>.csv`: по строке на сделку, в
/// порядке записи файла (хронологическом). Пустые сутки (нет ни одной
/// сделки) дают файл с одним заголовком — не ошибка.
pub fn run_trades(args: &TradesArgs) -> anyhow::Result<TradesSummary> {
    let plan::ResolvedPlan { files, tick_e9 } = plan::resolve(args)?;

    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("trades-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(TRADES_COLUMNS)?;

    let mut days_seen = std::collections::BTreeSet::new();
    let mut buy = 0usize;
    let mut sell = 0usize;
    for (path, day) in &files {
        days_seen.insert(day.clone());
        let data = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        loop {
            let Some(records) = read_frame_soft(&mut reader, path)? else {
                break;
            };
            for rec in &records {
                if !is_trade_ev(rec.ev) {
                    continue;
                }
                w.write_record(row::trade_row(day, rec, tick_e9))?;
                if rec.ev == LOCAL_BUY_TRADE_EVENT {
                    buy += 1;
                } else {
                    sell += 1;
                }
            }
        }
    }
    w.flush()?;

    Ok(TradesSummary {
        days: days_seen.len(),
        trades: buy + sell,
        buy,
        sell,
        out,
    })
}
