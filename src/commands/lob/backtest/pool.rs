//! Размер круга и поля лота пула, общие с `lob bounce-grid`: `order_size_22a`
//! (`pool_order_qty`, Decision 22а), поля лота символа из `instruments.csv`
//! (`PoolLot`) и номинал в долларах (`PoolLot::qty_usd_e9`, владелец 20.09).
//! Вынесено из `backtest` при разрезке B3 (ревью 23.09), поведение не
//! менялось.

use std::path::Path;

/// Размер круга `order_size_22a` (Decision 22а) от полей пула сессии и цены
/// последнего касания: `max(minOrderQty, ceil(minNotional / (step × price)) ×
/// step)`. Цена измерена на тех же данных, что и вердикт, — второй проход за
/// ценой не нужен (тот же приём, что у пилота с последней строкой
/// `levels-floor`). Нет символа в пуле — отказ: лот обязан быть названным
/// числом, а не шагом книги.
pub(crate) fn pool_order_qty(
    instruments_csv: &Path,
    symbol: &str,
    last_price_tick: i64,
    tick_e9: i64,
) -> anyhow::Result<i64> {
    let lot = PoolLot::read(instruments_csv, symbol)?;
    Ok(lot.qty_22a_e9(last_price_tick.saturating_mul(tick_e9)))
}

/// Поля лота символа из `instruments.csv` пула в 1e-9 — прочитаны один раз,
/// лот по цене считается без файла (R2: размер круга — по цене **каждого**
/// сигнала, а не одной ценой на символ).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PoolLot {
    pub(crate) min_order_qty_e9: i64,
    pub(crate) qty_step_e9: i64,
    pub(crate) min_notional_value_e9: i64,
}

impl PoolLot {
    pub(crate) fn read(instruments_csv: &Path, symbol: &str) -> anyhow::Result<Self> {
        let (min_order_qty_e9, qty_step_e9, min_notional_value_e9) =
            pool_lot_fields(instruments_csv, symbol)?;
        Ok(Self {
            min_order_qty_e9,
            qty_step_e9,
            min_notional_value_e9,
        })
    }

    /// `order_size_22a` (Decision 22а) при цене `price_e9`.
    pub(crate) fn qty_22a_e9(&self, price_e9: i64) -> i64 {
        crate::commands::lob::pick::order_size_22a(
            self.min_order_qty_e9,
            self.qty_step_e9,
            self.min_notional_value_e9,
            price_e9,
        )
    }

    /// Лот под номинал `usd` при цене `price_e9` (владелец 20.09: «$1000 в
    /// отчёте — масштабирование или сделка?»): `floor(usd / цена / шаг) × шаг`,
    /// не меньше лота 22а — модель очереди исполняет **реальный** размер, а не
    /// минимальный чек. Цена или шаг не положительны — лот 22а (у него тот же
    /// отказ от деления, `order_size_22a`); вызывающий, которому это ошибка,
    /// проверяет до вызова (`OrderSizing::from_args` сетки форм).
    pub(crate) fn qty_usd_e9(&self, price_e9: i64, usd: f64) -> i64 {
        let floor_e9 = self.qty_22a_e9(price_e9);
        if price_e9 <= 0 || self.qty_step_e9 <= 0 {
            return floor_e9;
        }
        // Шагов в номинале: floor(usd × 1e9 / price_e9 / step_e9).
        #[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
        let steps = ((usd * 1e18) / (price_e9 as f64) / (self.qty_step_e9 as f64)).floor() as i64;
        steps.saturating_mul(self.qty_step_e9).max(floor_e9)
    }
}

/// Поля лота символа из `instruments.csv` пула: `(min_order_qty, qty_step,
/// min_notional_value)` в 1e-9.
fn pool_lot_fields(instruments_csv: &Path, symbol: &str) -> anyhow::Result<(i64, i64, i64)> {
    #[derive(Debug, serde::Deserialize)]
    struct Row {
        symbol: String,
        min_order_qty: String,
        qty_step: String,
        min_notional_value: String,
    }
    let mut r = crate::commands::lob::pick::instruments_csv_reader(instruments_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
    for row in r.deserialize::<Row>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
        if row.symbol != symbol {
            continue;
        }
        let e9 = |name: &str, v: &str| {
            crate::bybit::ws::parse_e9(v)
                .ok_or_else(|| anyhow::anyhow!("{symbol}: {name} `{v}` не разобрался как 1e-9"))
        };
        return Ok((
            e9("min_order_qty", &row.min_order_qty)?,
            e9("qty_step", &row.qty_step)?,
            e9("min_notional_value", &row.min_notional_value)?,
        ));
    }
    anyhow::bail!(
        "{symbol}: нет в {} — лот из пула взять негде",
        instruments_csv.display()
    )
}
