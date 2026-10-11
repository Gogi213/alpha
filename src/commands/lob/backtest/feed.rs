//! Общие помощники движка, используемые и `lob backtest`, и `lob
//! bounce-grid`: заголовок бинлога и `ReplayFeed` (`read_tick_step`/
//! `open_replay_feed`), перевод `Feed` в события крейта
//! (`events_from_feed`/`count_feed_events`/
//! `count_feed_events_until`/`replay_compact_into_until`/`translate_feed_until`/
//! `push_side`). Вынесено из `backtest` при разрезке B3
//! (ревью 23.09), поведение не менялось.

use std::collections::HashSet;
use std::hash::{BuildHasherDefault, Hasher};
use std::path::{Path, PathBuf};

#[cfg(test)]
use hftbacktest::types::Event as HbtEvent;

use crate::binlog;
use crate::bybit::ws::{Event as WsEvent, Trade};
use crate::feed::{
    replay::{RawMarket, ReplayFeed},
    Event as FeedEvent, Feed,
};
use crate::lob::backtest::{CompactEvent, EventKind};

// ---------------------------------------------------------------------------
// Бинлог: заголовок (тик/лот) и `ReplayFeed`.
// ---------------------------------------------------------------------------

pub(crate) fn read_tick_step(path: &Path) -> anyhow::Result<(i64, i64)> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
    let reader = binlog::Reader::open(file)
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    Ok((header.tick_e9, header.step_e9))
}

/// Расписание шагов суток по частям (TK-037, В-172): `None` — ни одна часть не v4 (шаг один, сетка
/// данных; прежний путь). Часть v4 допустима только единственной в сутках и с заголовком = сетка
/// символа `(tick_e9, step_e9)`; иначе отказ — тихо взять не ту сетку нельзя.
pub(crate) fn read_day_schedule(
    paths: &[PathBuf],
    tick_e9: i64,
    step_e9: i64,
) -> anyhow::Result<Option<crate::lob::step_schedule::StepSchedule>> {
    let mut found = None;
    for path in paths {
        let file = std::fs::File::open(path)
            .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
        let reader = binlog::Reader::open(file)
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let sched = reader.step_schedule();
        if sched.is_empty() {
            continue;
        }
        let h = reader.header();
        anyhow::ensure!(
            paths.len() == 1 && h.tick_e9 == tick_e9 && h.step_e9 == step_e9,
            "{}: расписание шагов (v4) допустимо только в единственной части суток с сеткой символа              ({tick_e9}, {step_e9}); в файле ({}, {}), частей в сутках {}",
            path.display(),
            h.tick_e9,
            h.step_e9,
            paths.len()
        );
        found = Some(
            crate::lob::step_schedule::StepSchedule::from_header(h.tick_e9, h.step_e9, sched)
                .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?,
        );
    }
    Ok(found)
}

pub(crate) fn open_replay_feed(path: &Path) -> anyhow::Result<ReplayFeed<std::fs::File>> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
    ReplayFeed::open(0, file).map_err(|e| anyhow::anyhow!("бинлог {}: {e:?}", path.display()))
}

/// Переводит поток `Feed` в события крейта для `L2AssetBuilder`/
/// `Data::from_data`. `is_snapshot` требует явно обнулить уровни, которых
/// нет в новом снапшоте (то же правило, что `book::Book::apply` — очистка
/// перед применением): функция ведёт свой минимальный учёт видимых цен по
/// стороне только для этого обнуления — это перевод в события, не книга.
#[cfg(test)]
pub(crate) fn events_from_feed(feed: &mut dyn Feed) -> Vec<HbtEvent> {
    let mut out = Vec::new();
    translate_feed_until(feed, None, &mut |ev| out.push(ev.expand()));
    out
}

/// Сколько событий крейта даст `feed` — тот же перевод, что
/// `events_from_feed`, но в счётчик. Нужен, чтобы выделить `Vec` суток **один раз** точного
/// размера: рост удвоением держал бы старый и новый буфер вместе (до 3×
/// итога), и на сутках в 20 млн событий это 3 ГБ — сетка на сервере умирала
/// по OOM (2026-09-18, `alpha-grid-20260917`, лимит 2.6 ГБ). Второй декод
/// стоит ~0.16 мкс на событие — дешевле памяти.
pub(crate) fn count_feed_events(feed: &mut dyn Feed) -> usize {
    let mut n = 0usize;
    translate_feed_until(feed, None, &mut |_| n += 1);
    n
}

/// Как `count_feed_events`, но с потолком времени (`until_ns`, исключая):
/// перенос круга через полночь (`bounce_grid::carry_events`) читает сутки
/// D+1 не целиком, а только окно, нужное дочитать уже открытые круги суток
/// D — тот же риск OOM, что решает двухпроходный точный `Vec` `day_events`
/// (её doc), только здесь предел не «конец файла», а `until_ns`. Второе
/// значение — уткнулись ли в потолок (`true`) или файл кончился раньше
/// (`false`): части довеска хронологичны (`session_parts_for`), и как
/// только одна упёрлась в потолок, следующие только позже — читать их
/// незачем (см. `carry_events`).
pub(crate) fn count_feed_events_until(feed: &mut dyn Feed, until_ns: i64) -> (usize, bool) {
    let mut n = 0usize;
    let hit = translate_feed_until(feed, Some(until_ns), &mut |_| n += 1);
    (n, hit)
}

/// Перевод `feed` → события крейта, с необязательным потолком времени.
/// `until_ns` сравнивается с `local_ts_ns` события (тем же полем, что несёт
/// каждое рыночное событие) — как только оно дошло до потолка, перевод
/// останавливается немедленно (`true`), не декодируя остаток файла: он не
/// нужен звонящему (`day_events`/`carry_events`) и стоил бы памяти. `None` —
/// прежнее поведение, весь `feed` до конца (`false`).
pub(super) fn translate_feed_until(
    feed: &mut dyn Feed,
    until_ns: Option<i64>,
    sink: &mut impl FnMut(CompactEvent),
) -> bool {
    let mut known_bids = PriceSet::default();
    let mut known_asks = PriceSet::default();

    while let Some(ev) = feed.next_event() {
        let FeedEvent::Market {
            local_ts_ns,
            payload,
            ..
        } = ev
        else {
            continue;
        };
        if let Some(until) = until_ns {
            if local_ts_ns >= until {
                return true;
            }
        }
        match payload {
            // Р6: метка биржи — в мс, как пришла; `× 10⁶` делает `CompactEvent::expand`.
            WsEvent::Book(up) => emit_book(
                sink,
                &mut known_bids,
                &mut known_asks,
                up.is_snapshot,
                up.cts_ms,
                local_ts_ns,
                &up.bids,
                &up.asks,
            ),
            WsEvent::Trade(t) => emit_trade(sink, &t, local_ts_ns),
            WsEvent::Other | WsEvent::SubscribeFailed { .. } => {}
        }
    }
    false
}

#[allow(clippy::too_many_arguments)]
fn emit_book(
    sink: &mut impl FnMut(CompactEvent),
    known_bids: &mut PriceSet,
    known_asks: &mut PriceSet,
    is_snapshot: bool,
    exch_ms: i64,
    local_ts_ns: i64,
    bids: &[(i64, i64)],
    asks: &[(i64, i64)],
) {
    push_side(
        sink,
        known_bids,
        bids,
        is_snapshot,
        exch_ms,
        local_ts_ns,
        true,
    );
    push_side(
        sink,
        known_asks,
        asks,
        is_snapshot,
        exch_ms,
        local_ts_ns,
        false,
    );
}

fn emit_trade(sink: &mut impl FnMut(CompactEvent), t: &Trade, local_ts_ns: i64) {
    let kind = if t.aggressor_is_buy {
        EventKind::BuyTrade
    } else {
        EventKind::SellTrade
    };
    sink(CompactEvent::new(
        kind,
        t.exch_ms,
        local_ts_ns,
        t.price_e9,
        t.qty_e9,
    ));
}

/// Сутки из `ReplayFeed` в компактные события напрямую (`ReplayFeed::drive_raw`), без `Event`,
/// `Update` и очереди: тот же перевод (`emit_book`/`emit_trade`), те же события и порядок, что у
/// `translate_feed_until` (оракул — тест `direct_replay_path_matches_feed_translation`).
pub(crate) fn replay_compact_into_until<R: std::io::Read>(
    feed: &mut ReplayFeed<R>,
    until_ns: Option<i64>,
    out: &mut Vec<CompactEvent>,
) -> bool {
    let mut known_bids = PriceSet::default();
    let mut known_asks = PriceSet::default();
    let mut sink = |ev| out.push(ev);
    feed.drive_raw(&mut |local_ts_ns, ev| {
        if until_ns.is_some_and(|until| local_ts_ns >= until) {
            return true;
        }
        match ev {
            RawMarket::Book {
                is_snapshot,
                cts_ms,
                bids,
                asks,
            } => emit_book(
                &mut sink,
                &mut known_bids,
                &mut known_asks,
                is_snapshot,
                cts_ms,
                local_ts_ns,
                bids,
                asks,
            ),
            RawMarket::Trade(t) => emit_trade(&mut sink, &t, local_ts_ns),
        }
        false
    })
}

/// Множество цен известных уровней: нужно только «есть ли»; порядок обхода
/// не значим (stale сортируется перед выдачей, как раньше шёл `BTreeMap`).
type PriceSet = HashSet<i64, BuildHasherDefault<PriceHasher>>;

#[derive(Default)]
struct PriceHasher(u64);

impl Hasher for PriceHasher {
    fn finish(&self) -> u64 {
        // цены кратны тику (младшие биты нулевые) — старшие биты произведения в индекс
        self.0 ^ (self.0 >> 32)
    }
    fn write(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.0 = (self.0 ^ u64::from(b)).wrapping_mul(0x9E37_79B9_7F4A_7C15);
        }
    }
    fn write_i64(&mut self, v: i64) {
        self.0 = (v as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    }
}

#[allow(clippy::too_many_arguments)]
fn push_side(
    sink: &mut impl FnMut(CompactEvent),
    known: &mut PriceSet,
    rows: &[(i64, i64)],
    is_snapshot: bool,
    exch_ms: i64,
    local_ts: i64,
    bid: bool,
) {
    let kind = if bid {
        EventKind::BidDepth
    } else {
        EventKind::AskDepth
    };
    let mut push = |px: i64, qty: i64| {
        sink(CompactEvent::new(kind, exch_ms, local_ts, px, qty));
    };
    if is_snapshot {
        let fresh: PriceSet = rows.iter().map(|&(px, _)| px).collect();
        let mut stale: Vec<i64> = known
            .iter()
            .copied()
            .filter(|px| !fresh.contains(px))
            .collect();
        stale.sort_unstable();
        for px in stale {
            known.remove(&px);
            push(px, 0);
        }
    }
    for &(px, qty) in rows {
        if qty > 0 {
            known.insert(px);
        } else {
            known.remove(&px);
        }
        push(px, qty);
    }
}
