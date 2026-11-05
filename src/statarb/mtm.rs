//! Оценка по рынку позиции, оставшейся открытой на конце окна (TK-493, `statarb run --mtm-open`).
//!
//! Ядро такую позицию не закрывает и в P&L не включает (`Counters::open_at_end`). Здесь она оценивается как
//! граница чувствительности, не как основной результат: каждая нога закрывается по ПОСЛЕДНЕМУ валидному close
//! (не раньше своего входа), комиссия выхода — как у обычного выхода (`taker_out`), фандинг — накопленный к концу.
//! Формулы те же, что в ядре для выхода, цена выхода — close вместо open.

use super::kernel::{OpenPos, Params};

/// Копия `OpenPos`: приёмник держит её после выхода из ядра.
#[derive(Debug, Clone, PartialEq)]
pub struct OpenSnap {
    pub dir: i8,
    pub sig_in: i64,
    pub fill_in: i64,
    pub fill_x: Option<i64>,
    pub qty: Vec<f64>,
    pub pin: Vec<f64>,
    pub fund: f64,
}

impl OpenSnap {
    pub fn of(o: &OpenPos) -> OpenSnap {
        OpenSnap {
            dir: o.dir,
            sig_in: o.sig_in,
            fill_in: o.fill_in,
            fill_x: o.fill_x,
            qty: o.qty.to_vec(),
            pin: o.pin.to_vec(),
            fund: o.fund,
        }
    }
}

/// Результат оценки открытой позиции, bps номинала Y (как в `Trade`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Mtm {
    pub gross_bps: f64,
    pub fee_bps: f64,
    pub funding_bps: f64,
    pub net_bps: f64,
    /// Насколько давно (минут до конца окна) закрылась самая «старая» из ног, по чьему close оценена позиция:
    /// 0 — все ноги оценены по последней минуте окна; большое — контракт снят или молчит (G8b).
    pub stale_min: i64,
}

/// Последний конечный элемент `c` на индексах `≥ from`: `(индекс, значение)`.
pub fn last_valid(c: &[f64], from: usize) -> Option<(usize, f64)> {
    c.iter()
        .enumerate()
        .skip(from)
        .rev()
        .find(|(_, v)| v.is_finite())
        .map(|(i, v)| (i, *v))
}

/// Закрытия окна: `cy` — нога Y, `cx[i]` — компонента X; индекс `i` ↔ минута `first_min + i` (как `PairInput`).
/// Нет конечного close с минуты входа ноги (в панели такого не бывает: вход идёт по open той же свечи) — нога
/// оценена по цене входа, то есть с нулевым результатом.
pub fn value(p: &Params, o: &OpenSnap, first_min: i64, cy: &[f64], cx: &[&[f64]]) -> Mtm {
    let (Some((&qy, qx)), Some((&py, px))) = (o.qty.split_first(), o.pin.split_first()) else {
        return Mtm {
            gross_bps: 0.0,
            fee_bps: 0.0,
            funding_bps: o.fund * 1e4,
            net_bps: o.fund * 1e4,
            stale_min: 0,
        };
    };
    let end = cy.len() as i64 - 1;
    let from = |fill: i64| usize::try_from(fill - first_min).unwrap_or(0);
    let (iy, cly) = last_valid(cy, from(o.fill_in)).unwrap_or((from(o.fill_in), py));
    let mut oldest = iy as i64;
    let mut gross = qy * (cly - py);
    let mut fee =
        qy.abs() * (py * p.y_kind.fee_bps(p.taker_in) + cly * p.y_kind.fee_bps(p.taker_out));
    if let Some(fx) = o.fill_x {
        for ((&q, &pin), c) in qx.iter().zip(px).zip(cx) {
            let (ix, clx) = last_valid(c, from(fx)).unwrap_or((from(fx), pin));
            oldest = oldest.min(ix as i64);
            gross += q * (clx - pin);
            fee += q.abs()
                * (pin * p.x_kind.fee_bps(p.taker_in) + clx * p.x_kind.fee_bps(p.taker_out));
        }
    }
    let (gross_bps, funding_bps) = (gross * 1e4, o.fund * 1e4);
    Mtm {
        gross_bps,
        fee_bps: fee,
        funding_bps,
        net_bps: gross_bps - fee + funding_bps,
        stale_min: end - oldest,
    }
}
