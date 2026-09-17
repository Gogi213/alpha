//! `Feed` из бинлога: один суточный файл — один инструмент, ровно как их
//! пишет `lob session` (по файлу на инструмент). Реконструкция обновлений
//! книги переиспользует `bybit::verify::FileReplayer` — та же группировка
//! записей по `(is_snapshot, exch_ts_ns)` уже реализована и проверена там
//! (сверка книги, `interfaces.md` шов 3); писать её второй раз здесь было
//! бы Reinvention (пятое правило `interfaces.md`). Сделки восстанавливаются
//! отдельно, напрямую из бит `Record::ev` — предикат `bybit::verify::
//! is_trade_ev` общий с `commands::lob` (таск 17), а сборка `WsEvent::Trade`
//! здесь своя: `FileReplayer::push_frame` для своей задачи (проверка «сделка
//! внутри диапазона книги») хранит только цену и флаг блочности, а `Feed`
//! живой стороны отдаёт сделку целиком (сторона, размер, время) — другая
//! форма результата, не переиспользуемая как функция.

use std::collections::VecDeque;
use std::io::Read;

use hftbacktest::types::LOCAL_BUY_TRADE_EVENT;

use crate::binlog::{self, BinlogError, Record};
use crate::bybit::verify::{is_trade_ev, FileReplayer};
use crate::bybit::ws::{Event as WsEvent, Trade};

use super::live::{LayoutError, PoolMember};
use super::{DynamicPool, Event, Feed};

/// `Feed` с одного суточного файла бинлога одного инструмента пула.
pub struct ReplayFeed<R: Read> {
    symbol: u16,
    tick_e9: i64,
    step_e9: i64,
    reader: binlog::Reader<R>,
    replayer: FileReplayer,
    pending: VecDeque<Event>,
    last_local_ts_ns: i64,
    done: bool,
}

impl<R: Read> ReplayFeed<R> {
    /// `symbol` — тот же индекс пула, под которым файл писала `lob session`
    /// (см. doc модуля `feed`: тег события не имеет права быть `String`).
    pub fn open(symbol: u16, inner: R) -> Result<Self, BinlogError> {
        let reader = binlog::Reader::open(inner)?;
        let header = reader.header();
        Ok(Self {
            symbol,
            tick_e9: header.tick_e9,
            step_e9: header.step_e9,
            reader,
            replayer: FileReplayer::new(),
            pending: VecDeque::new(),
            last_local_ts_ns: 0,
            done: false,
        })
    }

    /// Один и тот же вызов на одну запись — `FileReplayer::push_frame`
    /// сам решает, закрывает ли эта запись группу (снапшот сменился на
    /// дельту, дельта на сделку, дельта на снапшот) или продолжает её,
    /// ровно так же, как `commands::lob::replay_symbol` уже это делает.
    fn push_record(&mut self, rec: &Record) {
        self.last_local_ts_ns = rec.local_ts_ns;
        let mut ups = Vec::new();
        let mut trade_points = Vec::new();
        self.replayer.push_frame(
            std::slice::from_ref(rec),
            self.tick_e9,
            self.step_e9,
            &mut ups,
            &mut trade_points,
        );
        for up in ups {
            self.pending.push_back(Event::Market {
                symbol: self.symbol,
                local_ts_ns: rec.local_ts_ns,
                parse_latency_ns: None,
                payload: WsEvent::Book(up),
            });
        }
        if is_trade_ev(rec.ev) {
            self.pending.push_back(Event::Market {
                symbol: self.symbol,
                local_ts_ns: rec.local_ts_ns,
                parse_latency_ns: None,
                payload: WsEvent::Trade(Trade {
                    exch_ms: rec.exch_ts_ns / 1_000_000,
                    price_e9: rec.price_ticks * self.tick_e9,
                    qty_e9: rec.qty_lots * self.step_e9,
                    aggressor_is_buy: rec.ev == LOCAL_BUY_TRADE_EVENT,
                    block: rec.block,
                    rpi: rec.rpi,
                }),
            });
        }
    }
}

impl<R: Read> Feed for ReplayFeed<R> {
    fn next_event(&mut self) -> Option<Event> {
        loop {
            if let Some(ev) = self.pending.pop_front() {
                return Some(ev);
            }
            if self.done {
                return None;
            }
            match self.reader.read_frame() {
                Ok(Some(records)) => {
                    for rec in &records {
                        self.push_record(rec);
                    }
                }
                Ok(None) => {
                    // Конец файла: слить незакрытую группу обновлений, если
                    // осталась (симметрично `commands::lob::replay_symbol`).
                    let mut tail = Vec::new();
                    self.replayer.finish(&mut tail);
                    for up in tail {
                        self.pending.push_back(Event::Market {
                            symbol: self.symbol,
                            local_ts_ns: self.last_local_ts_ns,
                            parse_latency_ns: None,
                            payload: WsEvent::Book(up),
                        });
                    }
                    self.done = true;
                }
                Err(_) => {
                    // Повреждённый хвост файла — уже отдельный шов
                    // (`binlog::Reader`, фаззер, `interfaces.md` шов 3).
                    // `Feed::next_event` не несёт `Result`, поэтому здесь
                    // поток просто заканчивается, без второго репортинга
                    // одной и той же ошибки.
                    self.done = true;
                }
            }
        }
    }
}

/// Бинлог уже записан — ни добавить инструмент, ни снять его на ходу нечем
/// (таск 34, A8.1).
impl<R: Read> DynamicPool for ReplayFeed<R> {
    fn add(&mut self, _members: Vec<PoolMember>) -> Result<Vec<u16>, LayoutError> {
        Err(LayoutError::StaticSource)
    }

    fn remove(&mut self, _symbols: &[u16]) -> Result<Vec<u16>, LayoutError> {
        Err(LayoutError::StaticSource)
    }
}

// -----------------------------------------------------------------------
// Грепом по образцу `lob/levels.rs::module_stays_detached_from_transport_
// clocks_and_approx_numbers`: горячий путь не вправе звать часы напрямую,
// представлять цену или размер числом с плавающей запятой, ни держать
// книгу в хеш-отображении (таск 17, долг таска 15 — «Из ремонта таска 15»,
// греп-тест раньше был только у `strategy.rs`/`react.rs`).
// -----------------------------------------------------------------------

#[cfg(test)]
mod hot_path_guard {
    /// Строки собраны из частей — иначе литерал триггерил бы эту же
    /// проверку сам на себя.
    #[test]
    fn module_never_calls_the_wall_clock_or_uses_float_prices_or_hashmaps() {
        const SRC: &str = include_str!("replay.rs");
        let banned = [
            concat!("Inst", "ant::now"),
            concat!("System", "Time::now"),
            concat!("f", "64"),
            concat!("Hash", "Map"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}

#[cfg(test)]
mod tests;
