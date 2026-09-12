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
