//! Компактное событие потока (Р6, T-17, 26.09; план — `docs/findings/backtest-optimization-2026-09-26.md`,
//! «План Р6», проверка Судьи `docs/research/reviews/backtest-optimization-2026-09-26.md`).
//!
//! `hftbacktest::types::Event` занимает 64 Б (`#[repr(C, align(64))]`), а в нашем потоке половина полей
//! постоянна: `order_id`/`ival`/`fval` — нули, `ev` — одно из четырёх значений, `exch_ts` — миллисекунды
//! биржи × 10⁶, `px`/`qty` — целые e9 ÷ 10⁹. `CompactEvent` хранит исходные целые (32 Б), а `expand`
//! повторяет ровно те операции, которыми перевод потока (`commands::lob::backtest::feed`) строил `Event`, —
//! поэтому развёртка совпадает бит в бит по построению: перевод сам идёт через `expand`, второй правды нет.
//!
//! Пятого вида события нет по построению: вид — перечисление `EventKind` из четырёх вариантов (условие Судьи 1).

use hftbacktest::types::{
    Event, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT, EXCH_EVENT,
    EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BUY_TRADE_EVENT,
    LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

/// Вид события потока — ровно четыре, по двум битам в `CompactEvent`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventKind {
    BidDepth = 0,
    AskDepth = 1,
    BuyTrade = 2,
    SellTrade = 3,
}

impl EventKind {
    /// Флаги `ev` крейта — те же, что ставил перевод потока.
    fn ev_bits(self) -> u64 {
        match self {
            EventKind::BidDepth => LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
            EventKind::AskDepth => LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT,
            EventKind::BuyTrade => {
                LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT | EXCH_EVENT | LOCAL_EVENT
            }
            EventKind::SellTrade => {
                LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT | EXCH_EVENT | LOCAL_EVENT
            }
        }
    }

    fn from_bits(bits: i64) -> Self {
        match bits & 3 {
            0 => EventKind::BidDepth,
            1 => EventKind::AskDepth,
            2 => EventKind::BuyTrade,
            _ => EventKind::SellTrade,
        }
    }
}

/// Предел `|exch_ms|`, при котором `exch_ms << 2` помещается в `i64`. Больше него `exch_ms × 10⁶`
/// всё равно насыщается (`saturating_mul`) — прижатие к пределу даёт тот же `exch_ts`.
const EXCH_MS_LIMIT: i64 = (1 << 61) - 1;

/// Событие потока в 32 Б; `expand` — побитно тот же `Event`, что строил перевод потока.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CompactEvent {
    local_ts: i64,
    /// `exch_ms << 2 | kind` (арифметический сдвиг обратно восстанавливает и отрицательные `exch_ms`).
    exch_kind: i64,
    px_e9: i64,
    qty_e9: i64,
}

impl CompactEvent {
    /// Событие из исходных целых перевода: метка биржи в мс, локальная метка в нс, цена и объём в e9.
    pub fn new(kind: EventKind, exch_ms: i64, local_ts: i64, px_e9: i64, qty_e9: i64) -> Self {
        let exch_ms = exch_ms.clamp(-EXCH_MS_LIMIT, EXCH_MS_LIMIT);
        Self {
            local_ts,
            exch_kind: (exch_ms << 2) | kind as i64,
            px_e9,
            qty_e9,
        }
    }

    pub fn kind(&self) -> EventKind {
        EventKind::from_bits(self.exch_kind)
    }

    pub fn local_ts(&self) -> i64 {
        self.local_ts
    }

    pub fn exch_ts(&self) -> i64 {
        (self.exch_kind >> 2).saturating_mul(1_000_000)
    }

    pub(crate) fn exch_ms(&self) -> i64 {
        self.exch_kind >> 2
    }

    pub(crate) fn px_e9(&self) -> i64 {
        self.px_e9
    }

    pub(crate) fn qty_e9(&self) -> i64 {
        self.qty_e9
    }

    pub fn px(&self) -> f64 {
        self.px_e9 as f64 / 1e9
    }

    pub fn qty(&self) -> f64 {
        self.qty_e9 as f64 / 1e9
    }

    /// `Event` крейта — те же операции, что у перевода потока до Р6 (`exch_ms.saturating_mul(10⁶)`,
    /// `x as f64 / 1e9`, нули в `order_id`/`ival`/`fval`).
    pub fn expand(&self) -> Event {
        Event {
            ev: self.kind().ev_bits(),
            exch_ts: self.exch_ts(),
            local_ts: self.local_ts,
            px: self.px(),
            qty: self.qty(),
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }
}

/// Строки событий суток для окон и кругов (Р6): `[Event]` крейта (прежний путь, тесты, `--driver full`)
/// или компактные `[CompactEvent]` (`bounce-grid`). `row` — строка в виде `Event` крейта; `as_events` —
/// занять строки без копии, если они уже в его формате.
pub trait EventRows {
    fn len(&self) -> usize;
    fn is_empty(&self) -> bool {
        self.len() == 0
    }
    fn row(&self, i: usize) -> Event;
    fn row_local_ts(&self, i: usize) -> i64;
    fn row_exch_ts(&self, i: usize) -> i64;
    fn as_events(&self) -> Option<&[Event]> {
        None
    }
    /// Индекс строки вида, с которой читать, если окно начинается с исходной строки `orig_start`
    /// (у урезанной ленты индексы сжаты, у полной — те же).
    fn skip_to(&self, orig_start: usize) -> usize {
        orig_start
    }
}

impl EventRows for [Event] {
    fn len(&self) -> usize {
        <[Event]>::len(self)
    }
    fn row(&self, i: usize) -> Event {
        self[i].clone()
    }
    fn row_local_ts(&self, i: usize) -> i64 {
        self[i].local_ts
    }
    fn row_exch_ts(&self, i: usize) -> i64 {
        self[i].exch_ts
    }
    fn as_events(&self) -> Option<&[Event]> {
        Some(self)
    }
}

impl EventRows for [CompactEvent] {
    fn len(&self) -> usize {
        <[CompactEvent]>::len(self)
    }
    fn row(&self, i: usize) -> Event {
        self[i].expand()
    }
    fn row_local_ts(&self, i: usize) -> i64 {
        self[i].local_ts()
    }
    fn row_exch_ts(&self, i: usize) -> i64 {
        self[i].exch_ts()
    }
}

/// Массивы и `Vec` — те же строки, что их срез (тесты и вызывающие без явного `[..]`).
macro_rules! rows_via_slice {
    () => {
        fn len(&self) -> usize {
            EventRows::len(self.as_slice())
        }
        fn row(&self, i: usize) -> Event {
            EventRows::row(self.as_slice(), i)
        }
        fn row_local_ts(&self, i: usize) -> i64 {
            EventRows::row_local_ts(self.as_slice(), i)
        }
        fn row_exch_ts(&self, i: usize) -> i64 {
            EventRows::row_exch_ts(self.as_slice(), i)
        }
        fn as_events(&self) -> Option<&[Event]> {
            EventRows::as_events(self.as_slice())
        }
    };
}

impl<const N: usize> EventRows for [Event; N] {
    rows_via_slice!();
}

impl EventRows for Vec<Event> {
    rows_via_slice!();
}

impl EventRows for Vec<CompactEvent> {
    rows_via_slice!();
}

#[cfg(test)]
mod tests;
