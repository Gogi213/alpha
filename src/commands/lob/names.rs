//! Имена сторон, исходов и смертей уровня в артефактах CSV (`bid`/`ask`,
//! `eaten`/`pulled`/`mixed`, …) и печать `Option<f64>`. Отдельно от `mod.rs`:
//! одни и те же строки пишут `levels`, `markout`, `profiles` — словарь один.

use crate::book::Side;
use crate::lob::levels::{DeathKind, Outcome};

pub(crate) fn side_name(side: Side) -> &'static str {
    match side {
        Side::Bid => "bid",
        Side::Ask => "ask",
    }
}

/// Сторона агрессора сделки (`TradeHit::aggressor_is_buy`/`Record.ev`) —
/// `buy`/`sell`, не путать с `side_name` (сторона книги `bid`/`ask`): лента
/// сделок (`lob trades`, T1) печатает эту сторону, не сторону уровня.
pub(crate) fn aggressor_side_name(is_buy: bool) -> &'static str {
    if is_buy {
        "buy"
    } else {
        "sell"
    }
}

pub(crate) fn outcome_name(outcome: Outcome) -> &'static str {
    match outcome {
        Outcome::Eaten => "eaten",
        Outcome::Pulled => "pulled",
        Outcome::Mixed => "mixed",
    }
}

pub(crate) fn death_name(death: DeathKind) -> &'static str {
    match death {
        DeathKind::BelowFraction => "below_fraction",
        DeathKind::LeftTop => "left_top",
    }
}

pub(crate) fn some_or_empty(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.6}")).unwrap_or_default()
}
