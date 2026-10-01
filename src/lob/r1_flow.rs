//! Состояние монеты для колонок R1 (TK-025): лента, книга, поток заявок.
//!
//! Определения колонок — `docs/findings/tk025-design-2026-10-02.md` (раздел «Колонки состояния монеты»),
//! порядок и имена — `r1::FLOW_NAMES`. Заготовка API: тело пишет кодер A, сигнатуры менять нельзя
//! (на них опираются `levels.rs` и тесты).

use crate::book::Side;
use crate::lob::levels::TradeHit;
use crate::lob::r1::{FLOW_N, R1_UNDEF};

/// Глубина снимка книги, которую R1 читает (`obi50_bp`).
pub const BOOK_DEPTH: usize = 50;

/// Кадр книги для R1: уровни `(тик, лоты)` каждой стороны, лучшая цена первой, не больше [`BOOK_DEPTH`].
#[derive(Debug, Clone, Copy)]
pub struct BookView<'a> {
    pub bids: &'a [(i64, i64)],
    pub asks: &'a [(i64, i64)],
}

/// Кольца ленты, потока заявок и снимок книги одной монеты за сутки. Ёмкость фиксирована при создании.
#[derive(Debug, Default)]
pub struct R1Flow {}

impl R1Flow {
    /// Создаётся один раз на монету (выделение допустимо только здесь).
    pub fn new() -> Box<Self> {
        Box::new(Self {})
    }

    /// Сделка ленты; вызывается из `LevelTracker::observe_trade` для каждой не-блочной сделки.
    pub fn on_trade(&mut self, _hit: &TradeHit) {}

    /// Кадр книги в момент `ms`; вызывается из `LevelTracker::begin_frame`.
    pub fn on_frame(&mut self, _ms: i64, _book: BookView<'_>) {}

    /// Значения [`r1::FLOW_NAMES`] на момент `t0_ms` для стены стороны `wall_side`.
    pub fn fill(&self, _wall_side: Side, _t0_ms: i64, out: &mut [i64; FLOW_N]) {
        out.fill(R1_UNDEF);
    }
}
