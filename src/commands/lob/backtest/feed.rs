//! Общие помощники движка, используемые и `lob backtest`, и `lob
//! bounce-grid`: заголовок бинлога и `ReplayFeed` (`read_tick_step`/
//! `open_replay_feed`), перевод `Feed` в события крейта
//! (`events_from_feed`/`count_feed_events`/`feed_events_into`/
//! `count_feed_events_until`/`feed_events_into_until`/`translate_feed_until`/
//! `push_side`) и модель исполнения `BacktestFillModel` (`profiles::FillModel`
//! поверх `lob::backtest`, таск 16). Вынесено из `backtest` при разрезке B3
//! (ревью 23.09), поведение не менялось.

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use hftbacktest::types::{
    Event as HbtEvent, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT,
    EXCH_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BUY_TRADE_EVENT, LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

use crate::binlog;
use crate::book::Side;
use crate::bybit::ws::Event as WsEvent;
use crate::feed::{replay::ReplayFeed, Event as FeedEvent, Feed};
use crate::lob::backtest::{
    build_backtest, drive_profile, DriveConfig, ExecLatency, QueueModelKind, Signal, SIGMA_LONG,
    SIGMA_SHORT,
};
use crate::lob::levels::LevelRecord;
use crate::lob::markout::MidSample;

use crate::commands::lob::profiles::FillModel;

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

pub(crate) fn open_replay_feed(path: &Path) -> anyhow::Result<ReplayFeed<std::fs::File>> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("бинлог {} не открывается: {e}", path.display()))?;
    ReplayFeed::open(0, file).map_err(|e| anyhow::anyhow!("бинлог {}: {e:?}", path.display()))
}

/// События крейта по всем частям сессии подряд (таск 22: `session_binlog_for`
/// отдаёт список, не один файл) — конкатенация, не второй `Feed`: `feed/`
/// вне зоны этого таска, а `ReplayFeed` не читает несколько файлов сам
/// (каждый несёт собственный заголовок), поэтому склейка на уровне уже
/// переведённых событий, здесь, а не в `feed::replay`.
pub(super) fn events_from_paths(paths: &[PathBuf]) -> anyhow::Result<Vec<HbtEvent>> {
    let mut events = Vec::new();
    for path in paths {
        let mut feed = open_replay_feed(path)?;
        events.extend(events_from_feed(&mut feed));
    }
    Ok(events)
}

// ---------------------------------------------------------------------------
// `BacktestFillModel` (таск 16) — `profiles::FillModel` поверх `lob::backtest`:
// соединяет таблицу профилей/шорт-лист с настоящей моделью очереди
// (`RiskAdverseQueueModel`), закрывая BLOCKERS таска 13 («требует реального
// книжного потока сессии, не только `mids`»).
// ---------------------------------------------------------------------------

/// σ по стороне книги (Decision 14) — тот же выбор, что `read_signals` делает
/// из строки `bid`/`ask` `signals_csv`.
fn sigma_of(side: Side) -> i8 {
    match side {
        Side::Bid => SIGMA_SHORT,
        Side::Ask => SIGMA_LONG,
    }
}

/// Естественный ключ уровня внутри одного символа (`interfaces.md`:
/// `LevelRecord` — «рождение, сторона, цена»). `birth_ms` — эпоховые мс
/// (`up.cts_ms` источника, не относительное время сессии), поэтому пары
/// разных сессий одного символа не сталкиваются: кэш общий на всю модель, не
/// per-сессия.
type LevelKey = (String, i8, i64, i64);

fn level_key(symbol: &str, rec: &LevelRecord) -> LevelKey {
    (
        symbol.to_string(),
        sigma_of(rec.side),
        rec.price_tick,
        rec.birth_ms,
    )
}

/// `FillModel` (`commands::lob::profiles`) поверх `lob::backtest`: гоняет
/// `strategy::on_event` через настоящую очередь `RiskAdverseQueueModel` на
/// книжном потоке сессии (не только `mids`) и отвечает `filled` по кэшу,
/// заполненному `prime_session` — один прогон движка на сессию
/// (`interfaces.md`, BLOCKERS таска 13), не на уровень.
///
/// Сигналы этого прогона — **все** размеченные уровни сессии в порядке
/// таймлайна, а не только уровни одного профиля: одна позиция за раз
/// (`drive_profile`) моделируется на настоящем потоке сигналов стратегии, а
/// не на подмножестве, отфильтрованном по оси профиля — иначе два разных
/// профиля (например `marginal:side=bid` и `marginal:size=…`), которым
/// принадлежит один и тот же уровень, увидели бы разные занятости позиции по
/// одному и тому же событию.
///
/// RTT — median/p95 (те же обязательные параметры, что `lob backtest`, без
/// умолчания, §9). `filled()` решает по медианной: тот же выбор, что уже
/// сделан `TableComparison`/`compare_with_table` («по медианной — основной
/// сценарий»). `p95_rtt_ns` сохранён для симметрии CLI и возможного будущего
/// потребителя (`p95_rtt_ns()`) — сегодня решение `filled()` не меняет.
#[derive(Debug)]
pub struct BacktestFillModel {
    median_rtt_ns: ExecLatency,
    p95_rtt_ns: ExecLatency,
    order_qty_e9: i64,
    cache: RefCell<BTreeMap<LevelKey, bool>>,
}

impl BacktestFillModel {
    pub fn new(median_rtt_ns: ExecLatency, p95_rtt_ns: ExecLatency, order_qty_e9: i64) -> Self {
        Self {
            median_rtt_ns,
            p95_rtt_ns,
            order_qty_e9,
            cache: RefCell::new(BTreeMap::new()),
        }
    }

    /// 95-й перцентиль RTT, задан вместе с медианной (см. doc структуры).
    pub fn p95_rtt_ns(&self) -> ExecLatency {
        self.p95_rtt_ns
    }

    /// Прогоняет движок один раз на уже переведённый в события крейта поток
    /// (тестовый шов: `prime_session` собирает `events`/`tick`/`lot` из
    /// файла и зовёт этот метод — юнит-тест кормит синтетику напрямую, без
    /// файла бинлога).
    pub(super) fn prime_from_events(
        &self,
        symbol: &str,
        events: &[HbtEvent],
        tick_size: f64,
        lot_size: f64,
        records: &[LevelRecord],
    ) {
        if events.is_empty() || records.is_empty() {
            return;
        }
        let order_qty = self.order_qty_e9 as f64 / 1e9;
        let cfg = DriveConfig {
            order_qty,
            first_order_id: 1,
            queue_model: QueueModelKind::RiskAdverse,
        };
        let signals: Vec<Signal> = records
            .iter()
            .map(|r| Signal {
                t0_ns: r.birth_ms.saturating_mul(1_000_000),
                sigma: sigma_of(r.side),
            })
            .collect();
        let mut bt = build_backtest(
            events,
            tick_size,
            lot_size,
            self.median_rtt_ns,
            cfg.queue_model,
        );
        let Ok(run) = drive_profile(&mut bt, 0, &signals, &cfg) else {
            return;
        };
        // `drive_profile` сортирует сигналы по `t0_ns` стабильно и кладёт
        // ровно одну `FillObservation` на сигнал из этого порядка, пока не
        // упрётся в конец данных (`incomplete`) — тогда хвост остаётся без
        // наблюдения. Тот же стабильный порядок на исходных индексах
        // восстанавливает, какая запись какому наблюдению отвечает, без
        // повторного прогона движка (см. doc структуры).
        let mut order_idx: Vec<usize> = (0..records.len()).collect();
        order_idx.sort_by_key(|&i| signals[i].t0_ns);

        let mut cache = self.cache.borrow_mut();
        for (k, obs) in run.observations.iter().enumerate() {
            let Some(&orig) = order_idx.get(k) else {
                break;
            };
            let rec = &records[orig];
            cache.insert(level_key(symbol, rec), obs.filled);
        }
    }
}

impl FillModel for BacktestFillModel {
    /// Таск 22: сессия может нести несколько частей (`session_binlog_for`) —
    /// тик/лот берутся из заголовка первой части (части одной сессии не
    /// меняют шаги, в отличие от ротации `lob record` по смене шагов), а
    /// событийный поток — конкатенация всех частей по порядку
    /// (`events_from_paths`), один прогон движка на всю сессию, как раньше
    /// на один файл.
    fn prime_session(&self, symbol: &str, binlog_paths: &[PathBuf], records: &[LevelRecord]) {
        if records.is_empty() {
            return;
        }
        let Some(first) = binlog_paths.first() else {
            return;
        };
        let Ok((tick_e9, step_e9)) = read_tick_step(first) else {
            return;
        };
        let Ok(events) = events_from_paths(binlog_paths) else {
            return;
        };
        self.prime_from_events(
            symbol,
            &events,
            tick_e9 as f64 / 1e9,
            step_e9 as f64 / 1e9,
            records,
        );
    }

    fn filled(&self, symbol: &str, rec: &LevelRecord, _mids: &[MidSample]) -> Option<bool> {
        self.cache.borrow().get(&level_key(symbol, rec)).copied()
    }

    fn label(&self) -> &'static str {
        "backtest"
    }
}

// ---------------------------------------------------------------------------
// `Feed` (снапшоты/дельты стакана, сделки) → события `hftbacktest`. Цена и
// размер уже в 1e-9 у источника (`book::Update`/`bybit::ws::Trade`) — делить
// на 1e9 единственный раз, на границе `MarketDepth` (A7).
// ---------------------------------------------------------------------------

/// Переводит поток `Feed` в события крейта для `L2AssetBuilder`/
/// `Data::from_data`. `is_snapshot` требует явно обнулить уровни, которых
/// нет в новом снапшоте (то же правило, что `book::Book::apply` — очистка
/// перед применением): функция ведёт свой минимальный учёт видимых цен по
/// стороне только для этого обнуления — это перевод в события, не книга.
pub(crate) fn events_from_feed(feed: &mut dyn Feed) -> Vec<HbtEvent> {
    let mut out = Vec::new();
    feed_events_into(feed, &mut out);
    out
}

/// Сколько событий крейта даст `feed` — тот же перевод, что `feed_events_into`,
/// но в счётчик. Нужен, чтобы выделить `Vec` суток **один раз** точного
/// размера: рост удвоением держал бы старый и новый буфер вместе (до 3×
/// итога), и на сутках в 20 млн событий это 3 ГБ — сетка на сервере умирала
/// по OOM (2026-09-18, `alpha-grid-20260917`, лимит 2.6 ГБ). Второй декод
/// стоит ~0.16 мкс на событие — дешевле памяти.
pub(crate) fn count_feed_events(feed: &mut dyn Feed) -> usize {
    let mut n = 0usize;
    translate_feed_until(feed, None, &mut |_| n += 1);
    n
}

/// Перевод `feed` → события крейта в готовый `Vec` (без промежуточного).
pub(crate) fn feed_events_into(feed: &mut dyn Feed, out: &mut Vec<HbtEvent>) {
    translate_feed_until(feed, None, &mut |ev| out.push(ev));
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

/// Как `feed_events_into`, но с тем же потолком `until_ns` (см.
/// `count_feed_events_until`); возвращает, уткнулись ли в потолок.
pub(crate) fn feed_events_into_until(
    feed: &mut dyn Feed,
    until_ns: i64,
    out: &mut Vec<HbtEvent>,
) -> bool {
    translate_feed_until(feed, Some(until_ns), &mut |ev| out.push(ev))
}

/// Перевод `feed` → события крейта, с необязательным потолком времени.
/// `until_ns` сравнивается с `local_ts_ns` события (тем же полем, что несёт
/// каждое рыночное событие) — как только оно дошло до потолка, перевод
/// останавливается немедленно (`true`), не декодируя остаток файла: он не
/// нужен звонящему (`day_events`/`carry_events`) и стоил бы памяти. `None` —
/// прежнее поведение, весь `feed` до конца (`false`).
fn translate_feed_until(
    feed: &mut dyn Feed,
    until_ns: Option<i64>,
    sink: &mut impl FnMut(HbtEvent),
) -> bool {
    let mut known_bids: BTreeMap<i64, i64> = BTreeMap::new();
    let mut known_asks: BTreeMap<i64, i64> = BTreeMap::new();

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
            WsEvent::Book(up) => {
                let exch_ts = up.cts_ms.saturating_mul(1_000_000);
                push_side(
                    sink,
                    &mut known_bids,
                    &up.bids,
                    up.is_snapshot,
                    exch_ts,
                    local_ts_ns,
                    true,
                );
                push_side(
                    sink,
                    &mut known_asks,
                    &up.asks,
                    up.is_snapshot,
                    exch_ts,
                    local_ts_ns,
                    false,
                );
            }
            WsEvent::Trade(t) => {
                let ev_bits = (if t.aggressor_is_buy {
                    LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
                } else {
                    LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
                }) | EXCH_EVENT
                    | LOCAL_EVENT;
                sink(HbtEvent {
                    ev: ev_bits,
                    exch_ts: t.exch_ms.saturating_mul(1_000_000),
                    local_ts: local_ts_ns,
                    px: t.price_e9 as f64 / 1e9,
                    qty: t.qty_e9 as f64 / 1e9,
                    order_id: 0,
                    ival: 0,
                    fval: 0.0,
                });
            }
            WsEvent::Other | WsEvent::SubscribeFailed { .. } => {}
        }
    }
    false
}

#[allow(clippy::too_many_arguments)]
fn push_side(
    sink: &mut impl FnMut(HbtEvent),
    known: &mut BTreeMap<i64, i64>,
    rows: &[(i64, i64)],
    is_snapshot: bool,
    exch_ts: i64,
    local_ts: i64,
    bid: bool,
) {
    let ev_bits = if bid {
        LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
    } else {
        LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
    };
    let mut push = |px: i64, qty: i64| {
        sink(HbtEvent {
            ev: ev_bits,
            exch_ts,
            local_ts,
            px: px as f64 / 1e9,
            qty: qty as f64 / 1e9,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        });
    };
    if is_snapshot {
        let fresh: BTreeSet<i64> = rows.iter().map(|&(px, _)| px).collect();
        let stale: Vec<i64> = known
            .keys()
            .copied()
            .filter(|px| !fresh.contains(px))
            .collect();
        for px in stale {
            known.remove(&px);
            push(px, 0);
        }
    }
    for &(px, qty) in rows {
        if qty > 0 {
            known.insert(px, qty);
        } else {
            known.remove(&px);
        }
        push(px, qty);
    }
}
