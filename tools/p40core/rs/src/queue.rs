//! TK-500 П-40 §3.3: модель очереди `prob:3` — перенос `ProbQueueModel<PowerProbQueueFunc(3)>` из vendor/hftbacktest/src/backtest/models/queue.rs
//! (new_order / trade / depth / is_filled) и правил заполнения лимитки (сделка по цене лимитки сдвигает очередь, сделка/лучшая цена за ней — исполняет).
//! Один заказ — одна структура, без аллокаций на событие. Единицы qty — те, что в книге и в сделках ноги (у OKX — контракты, внутри ноги согласованы).

pub const POW: f64 = 3.0;

#[derive(Clone, Debug)]
pub struct QueueOrder {
    pub buy: bool,
    pub price: f64,
    lot: f64,
    front: f64,
    cum_trade: f64,
}

#[inline]
fn round_half_away(x: f64) -> f64 {
    x.round()
}

impl QueueOrder {
    /// Лимитка ставится на `price` при показанном на уровне объёме `level_qty` (вся очередь впереди).
    pub fn new(buy: bool, price: f64, level_qty: f64, lot: f64) -> Self {
        QueueOrder { buy, price, lot, front: level_qty, cum_trade: 0.0 }
    }

    pub fn front(&self) -> f64 {
        self.front
    }

    /// Сделка на ноге. `buyer_maker` — агрессор продавал (бьёт по бидам). Возвращает исполнено ли (полностью: частичных исполнений нет, как в NoPartialFill).
    pub fn on_trade(&mut self, tprice: f64, qty: f64, buyer_maker: bool) -> bool {
        // лимитка на покупку исполняется сделками продавца-агрессора, на продажу — покупателя-агрессора
        if self.buy != buyer_maker {
            return false;
        }
        let through = if self.buy { tprice < self.price } else { tprice > self.price };
        if through {
            return true;
        }
        if tprice == self.price {
            self.front -= qty;
            self.cum_trade += qty;
            return self.is_filled();
        }
        false
    }

    /// Изменение объёма на уровне лимитки `prev_qty → new_qty` (обновление книги).
    pub fn on_depth(&mut self, prev_qty: f64, new_qty: f64) {
        let mut chg = prev_qty - new_qty;
        // изменение объёма сделками уже учтено в on_trade — вычесть, чтобы не считать дважды
        chg -= self.cum_trade;
        self.cum_trade = 0.0;
        if chg < 0.0 {
            self.front = self.front.min(new_qty);
            return;
        }
        let front = self.front;
        let back = prev_qty - front;
        let mut prob = back.powf(POW) / (back.powf(POW) + front.powf(POW));
        if prob.is_infinite() {
            prob = 1.0;
        }
        if prob.is_nan() {
            // front = back = 0: prob = 0/0; как в исходнике, NaN попадает в est_front — берём предел min(new_qty) без движения очереди
            self.front = self.front.min(new_qty);
            return;
        }
        let est_front = front - (1.0 - prob) * chg + (back - prob * chg).min(0.0);
        self.front = est_front.min(new_qty);
    }

    /// Лучшая цена противоположной стороны зашла за лимитку (ask ≤ цена покупки, bid ≥ цена продажи) — исполнение по цене лимитки.
    pub fn on_cross(&self, best_opposite: f64) -> bool {
        if self.buy {
            best_opposite <= self.price
        } else {
            best_opposite >= self.price
        }
    }

    fn is_filled(&mut self) -> bool {
        let exec = round_half_away(-self.front / self.lot) as i64;
        if exec > 0 {
            self.front = 0.0;
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trade_at_price_drains_front_then_fills() {
        let mut o = QueueOrder::new(true, 10.0, 100.0, 1.0);
        assert!(!o.on_trade(10.0, 60.0, true));
        assert_eq!(o.front(), 40.0);
        assert!(!o.on_trade(10.0, 40.0, true)); // front = 0: не исполнена
        assert!(o.on_trade(10.0, 1.0, true)); // front = -1 → exec = 1
    }

    #[test]
    fn wrong_aggressor_side_does_not_move() {
        let mut o = QueueOrder::new(true, 10.0, 100.0, 1.0);
        assert!(!o.on_trade(10.0, 500.0, false)); // покупатель-агрессор бьёт по асам
        assert_eq!(o.front(), 100.0);
        let mut s = QueueOrder::new(false, 10.0, 100.0, 1.0);
        assert!(!s.on_trade(10.0, 500.0, true));
        assert_eq!(s.front(), 100.0);
    }

    #[test]
    fn trade_through_price_fills_at_once() {
        let mut b = QueueOrder::new(true, 10.0, 100.0, 1.0);
        assert!(b.on_trade(9.9, 1.0, true));
        let mut s = QueueOrder::new(false, 10.0, 100.0, 1.0);
        assert!(s.on_trade(10.1, 1.0, false));
        assert!(!s.on_trade(10.0, 1.0, false)); // по цене лимитки — только очередь
    }

    #[test]
    fn depth_increase_caps_front_at_new_qty_only() {
        let mut o = QueueOrder::new(true, 10.0, 100.0, 1.0);
        o.on_depth(100.0, 150.0); // добавили сзади
        assert_eq!(o.front(), 100.0);
        o.on_depth(150.0, 50.0); // chg = 100, front=100, back=50, prob=back^3/(back^3+front^3)=1/9
        let p = 125000.0 / (125000.0 + 1_000_000.0);
        let est = 100.0 - (1.0 - p) * 100.0 + (50.0 - p * 100.0_f64).min(0.0);
        assert!((o.front() - est.min(50.0)).abs() < 1e-9);
    }

    #[test]
    fn depth_decrease_after_trade_not_double_counted() {
        let mut o = QueueOrder::new(true, 10.0, 100.0, 1.0);
        o.on_trade(10.0, 30.0, true);
        assert_eq!(o.front(), 70.0);
        o.on_depth(100.0, 70.0); // уровень упал ровно на объём сделки: chg = 0 → очередь не двигается
        assert_eq!(o.front(), 70.0);
    }

    #[test]
    fn front_of_queue_cancel_all_back_is_zero() {
        // лимитка последняя в очереди: back = 0 → prob = 0 → весь chg уходит из front
        let mut o = QueueOrder::new(false, 5.0, 40.0, 1.0);
        o.on_depth(40.0, 30.0);
        assert!((o.front() - 30.0).abs() < 1e-9);
    }

    #[test]
    fn empty_level_nan_is_safe() {
        let mut o = QueueOrder::new(true, 10.0, 0.0, 1.0);
        o.on_depth(0.0, 0.0);
        assert_eq!(o.front(), 0.0);
    }

    #[test]
    fn cross_by_opposite_best() {
        let b = QueueOrder::new(true, 10.0, 1.0, 1.0);
        assert!(b.on_cross(10.0) && b.on_cross(9.9) && !b.on_cross(10.1));
        let s = QueueOrder::new(false, 10.0, 1.0, 1.0);
        assert!(s.on_cross(10.0) && s.on_cross(10.1) && !s.on_cross(9.9));
    }
}
