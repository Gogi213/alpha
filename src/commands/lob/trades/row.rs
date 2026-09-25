//! Строка `trades-<SYMBOL>.csv` (T1, П-02): пара «имя колонки → значение»
//! на позицию, тем же приёмом, что `touches/row.rs` — переставить имя без
//! значения уже негде.

use hftbacktest::types::LOCAL_BUY_TRADE_EVENT;

use crate::binlog::Record;
use crate::commands::lob::aggressor_side_name;

use super::{TRADES_COLUMNS, TRADES_WIDTH};

/// Цена сделки в $ — целочисленно, без `f64` в числителе (А1): `price_ticks
/// × tick_e9` в `i128` (тик заголовка не ограничивает результат `i64`),
/// дальше точное деление на `1e9` строкой — тот же приём, что
/// `pick/table.rs::format_e9`, здесь своя маленькая копия (та же причина,
/// что там: другая точка использования, общий обратный — только у
/// `bybit::ws::parse_e9`).
fn format_price_usd(price_ticks: i64, tick_e9: i64) -> String {
    let scaled = price_ticks as i128 * tick_e9 as i128;
    let neg = scaled < 0;
    let abs = scaled.unsigned_abs();
    let int_part = abs / 1_000_000_000;
    let frac_part = abs % 1_000_000_000;
    let mut s = if frac_part == 0 {
        int_part.to_string()
    } else {
        let mut frac_str = format!("{frac_part:09}");
        while frac_str.ends_with('0') {
            frac_str.pop();
        }
        format!("{int_part}.{frac_str}")
    };
    if neg {
        s.insert(0, '-');
    }
    s
}

/// Пары «имя колонки → значение» одной строки сделки, в порядке
/// `TRADES_COLUMNS`: запись как есть, без агрегации (T1 — сырой дамп;
/// агрегацию, CVD/окна, делает python поверх этого CSV).
fn trade_row_pairs(
    day_utc: &str,
    rec: &Record,
    tick_e9: i64,
) -> [(&'static str, String); TRADES_WIDTH] {
    let is_buy = rec.ev == LOCAL_BUY_TRADE_EVENT;
    [
        ("day_utc", day_utc.to_string()),
        ("exch_ts_ms", (rec.exch_ts_ns / 1_000_000).to_string()),
        ("local_ts_ms", (rec.local_ts_ns / 1_000_000).to_string()),
        ("side", aggressor_side_name(is_buy).to_string()),
        ("price_tick", rec.price_ticks.to_string()),
        ("price_usd", format_price_usd(rec.price_ticks, tick_e9)),
        ("qty_lots", rec.qty_lots.to_string()),
        ("block", rec.block.to_string()),
        ("rpi", rec.rpi.to_string()),
    ]
}

/// Строка `trades-<SYMBOL>.csv`, значения без имён, в порядке
/// `TRADES_COLUMNS` — для `csv::Writer::write_record`.
pub(super) fn trade_row(day_utc: &str, rec: &Record, tick_e9: i64) -> [String; TRADES_WIDTH] {
    let pairs = trade_row_pairs(day_utc, rec, tick_e9);
    debug_assert_eq!(
        pairs.each_ref().map(|(name, _)| *name),
        TRADES_COLUMNS,
        "имя колонки и её значение обязаны стоять на одной позиции"
    );
    pairs.map(|(_, v)| v)
}

/// Имена колонок из пар — только для теста, что порядок совпадает с
/// `TRADES_COLUMNS` независимо от профиля сборки (`debug_assert` не
/// выполняется в `--release`, которым гоняются тесты проекта, `CLAUDE.md`).
#[cfg(test)]
pub(super) fn trade_row_pair_names(
    day_utc: &str,
    rec: &Record,
    tick_e9: i64,
) -> [&'static str; TRADES_WIDTH] {
    trade_row_pairs(day_utc, rec, tick_e9).map(|(name, _)| name)
}
