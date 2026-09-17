//! `lob verify` (шаг 0.6 плана): сверка книги с REST по `u`, инварианты, трейды.
//!
//! Три проверки PLAN.md 0.6: (1) книга, проигранная ровно до `u` снапшота, сравнивается
//! с топ-50 REST; (2) инварианты на каждом событии; (3) сделка внутри диапазона,
//! накрытого книгой, стоит на удерживаемой цене (ревизия 17а: вне диапазона —
//! доля без порога, нарушение — внутри на недержимой цене). Чистая логика без сети
//! и часов: транспорт и чтение дейлогов — дело вызывающего (CLI в `src/commands`),
//! сюда приходят разобранные сущности.
//!
//! Оговорка про `u` (важно): суточный файл шага 0.3 `u` не хранит. Точнее, чем
//! «`order_id`/`ival` заняты»: `order_id` на книжных событиях безусловно ноль
//! (`record.rs`), а хранить `u` отдельным полем не стоит сознательно — поле пишется
//! сырым uvarint без дельты, и `u` порядка 10^9 это плюс пять байт на событие против
//! бюджета Decision 23. Поэтому файловый реплей идёт с синтетическими порядковыми
//! `u` и покрывает проверки 2-3. Проверка 1 требует настоящего `u` и
//! работает только на живом потоке — несовпадение `u` это ошибка выравнивания, а не
//! «грязная книга». Живой прогон на час из done-condition здесь не выполнялся: ему
//! нужны сеть и VPS, в песочнице его заменяет `#[ignore]`-тест на живом REST.

use std::path::{Path, PathBuf};

use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT,
};

use crate::binlog::Record;
use crate::book::{Book, Side, Update};
use crate::bybit::conn::ORDERBOOK_DEPTH;
use crate::bybit::rest::OrderbookSnapshot;

// ---------------------------------------------------------------------------
// Проверка 1: книга против REST-снапшота, сравнение в целых 1e-9
// ---------------------------------------------------------------------------

/// Расхождение одного уровня. `None` с любой стороны — уровня нет там вообще
/// (дыра в книге или лишний уровень), а не «нулевой размер»: ноль в книге
/// означает удаление, и такая запись в сравнении не участвует.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelMismatch {
    pub side: Side,
    pub tick: i64,
    pub snapshot_qty_e9: Option<i64>,
    pub book_qty_e9: Option<i64>,
}

/// Итог сверки. Сравнение объединением тиков (книга ∪ снапшот): один лишний
/// уровень даёт ровно одно расхождение, а не каскад. Именно так читается
/// порог «ноль расхождений» плана.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SnapshotDiff {
    pub bid_mismatches: Vec<LevelMismatch>,
    pub ask_mismatches: Vec<LevelMismatch>,
}

impl SnapshotDiff {
    pub fn is_clean(&self) -> bool {
        self.bid_mismatches.is_empty() && self.ask_mismatches.is_empty()
    }

    pub fn total(&self) -> usize {
        self.bid_mismatches.len() + self.ask_mismatches.len()
    }
}

/// Сравнение объединением тиков топ-50 книги с топ-50 снапшота. Для каждого
/// тика из объединения: есть в обоих — mismatch при разных qty; только с одной
/// стороны — mismatch с `None`. Порядок детерминирован: по убыванию тика
/// внутри каждой стороны. Сравнение идёт в 1e-9, поэтому неокруглимо:
/// расхождение означает расхождение данных, а не арифметики.
pub fn compare_with_snapshot(
    book: &Book,
    snap: &OrderbookSnapshot,
    tick_e9: i64,
    step_e9: i64,
) -> SnapshotDiff {
    debug_assert!(tick_e9 > 0 && step_e9 > 0);
    let mut out = SnapshotDiff::default();
    compare_side(
        Side::Bid,
        &top_bids_e9(book, tick_e9, step_e9),
        &snap.bids,
        tick_e9,
        &mut out.bid_mismatches,
    );
    compare_side(
        Side::Ask,
        &top_asks_e9(book, tick_e9, step_e9),
        &snap.asks,
        tick_e9,
        &mut out.ask_mismatches,
    );
    out
}

/// Лучшие 50 бидов книги как (тик, цена 1e-9, размер 1e-9), от лучшей цены.
fn top_bids_e9(book: &Book, tick_e9: i64, step_e9: i64) -> Vec<(i64, i64, i64)> {
    let mut v: Vec<(i64, i64, i64)> = book
        .levels(Side::Bid)
        .map(|(t, q)| (t, t * tick_e9, q * step_e9))
        .collect();
    v.sort_by(|a, b| b.0.cmp(&a.0));
    v.truncate(50);
    v
}

/// Лучшие 50 асков книги, от лучшей цены.
fn top_asks_e9(book: &Book, tick_e9: i64, step_e9: i64) -> Vec<(i64, i64, i64)> {
    let mut v: Vec<(i64, i64, i64)> = book
        .levels(Side::Ask)
        .map(|(t, q)| (t, t * tick_e9, q * step_e9))
        .collect();
    v.sort_by(|a, b| a.0.cmp(&b.0));
    v.truncate(50);
    v
}

/// Сравнение стороны объединением тиков (книга ∪ снапшот): один лишний уровень
/// в глубине даёт ровно одно расхождение, а не каскад сдвинутых позиций.
/// Тик снапшота восстанавливается делением на шаг (снимок биржи по построению
/// на тиках; неделимый остаток фиксируется как mismatch того же тика).
/// Порядок детерминирован: по убыванию тика.
fn compare_side(
    side: Side,
    book: &[(i64, i64, i64)],
    snap: &[(i64, i64)],
    tick_e9: i64,
    mismatches: &mut Vec<LevelMismatch>,
) {
    debug_assert!(tick_e9 > 0);
    use std::collections::{BTreeMap, BTreeSet};
    let mut book_map: BTreeMap<i64, i64> = BTreeMap::new();
    for &(tick, _, qty_e9) in book {
        book_map.insert(tick, qty_e9);
    }
    let mut snap_map: BTreeMap<i64, i64> = BTreeMap::new();
    let mut off_tick: BTreeSet<i64> = BTreeSet::new();
    for &(s_px, s_qty) in snap {
        let tick = s_px.div_euclid(tick_e9);
        snap_map.insert(tick, s_qty);
        if s_px.rem_euclid(tick_e9) != 0 {
            off_tick.insert(tick);
        }
    }
    let mut union: BTreeSet<i64> = BTreeSet::new();
    union.extend(book_map.keys().copied());
    union.extend(snap_map.keys().copied());
    for tick in union.into_iter().rev() {
        let b = book_map.get(&tick).copied();
        let s = snap_map.get(&tick).copied();
        if off_tick.contains(&tick) {
            mismatches.push(LevelMismatch {
                side,
                tick,
                snapshot_qty_e9: s,
                book_qty_e9: b,
            });
            continue;
        }
        if b != s {
            mismatches.push(LevelMismatch {
                side,
                tick,
                snapshot_qty_e9: s,
                book_qty_e9: b,
            });
        }
    }
}

/// Скобочное сравнение для живого тика: уровень — mismatch, только если
/// отличается от ОБОИХ состояний (`snap_qty` vs `before_qty` vs `after_qty`,
/// `None` — отдельное значение). Объединение трёх множеств:
/// `before` ∪ `after` ∪ snapshot. `book_qty` в строке — qty состояния `before`
/// (семантика колонки не меняется). `after = None` сводится к обычному
/// объединению `before` ∪ snapshot.
pub fn compare_with_bracket(
    before: &Book,
    after: Option<&Book>,
    snap: &OrderbookSnapshot,
    tick_e9: i64,
    step_e9: i64,
) -> SnapshotDiff {
    debug_assert!(tick_e9 > 0 && step_e9 > 0);
    let mut out = SnapshotDiff::default();
    let before_bids = top_bids_e9(before, tick_e9, step_e9);
    let before_asks = top_asks_e9(before, tick_e9, step_e9);
    let after_bids = after.map(|b| top_bids_e9(b, tick_e9, step_e9));
    let after_asks = after.map(|b| top_asks_e9(b, tick_e9, step_e9));
    compare_side_bracket(
        Side::Bid,
        &before_bids,
        after_bids.as_deref(),
        &snap.bids,
        tick_e9,
        &mut out.bid_mismatches,
    );
    compare_side_bracket(
        Side::Ask,
        &before_asks,
        after_asks.as_deref(),
        &snap.asks,
        tick_e9,
        &mut out.ask_mismatches,
    );
    out
}

fn compare_side_bracket(
    side: Side,
    before: &[(i64, i64, i64)],
    after: Option<&[(i64, i64, i64)]>,
    snap: &[(i64, i64)],
    tick_e9: i64,
    mismatches: &mut Vec<LevelMismatch>,
) {
    let Some(after_slice) = after else {
        return compare_side(side, before, snap, tick_e9, mismatches);
    };
    debug_assert!(tick_e9 > 0);
    use std::collections::{BTreeMap, BTreeSet};
    let mut before_map: BTreeMap<i64, i64> = BTreeMap::new();
    for &(tick, _, qty_e9) in before {
        before_map.insert(tick, qty_e9);
    }
    let mut after_map: BTreeMap<i64, i64> = BTreeMap::new();
    for &(tick, _, qty_e9) in after_slice {
        after_map.insert(tick, qty_e9);
    }
    let mut snap_map: BTreeMap<i64, i64> = BTreeMap::new();
    let mut off_tick: BTreeSet<i64> = BTreeSet::new();
    for &(s_px, s_qty) in snap {
        let tick = s_px.div_euclid(tick_e9);
        snap_map.insert(tick, s_qty);
        if s_px.rem_euclid(tick_e9) != 0 {
            off_tick.insert(tick);
        }
    }
    let mut union: BTreeSet<i64> = BTreeSet::new();
    union.extend(before_map.keys().copied());
    union.extend(after_map.keys().copied());
    union.extend(snap_map.keys().copied());
    for tick in union.into_iter().rev() {
        let b = before_map.get(&tick).copied();
        let a = after_map.get(&tick).copied();
        let s = snap_map.get(&tick).copied();
        if off_tick.contains(&tick) {
            mismatches.push(LevelMismatch {
                side,
                tick,
                snapshot_qty_e9: s,
                book_qty_e9: b,
            });
            continue;
        }
        if s != b && s != a {
            mismatches.push(LevelMismatch {
                side,
                tick,
                snapshot_qty_e9: s,
                book_qty_e9: b,
            });
        }
    }
}

// ---------------------------------------------------------------------------
// Проверка 2: инварианты книги на каждом событии
// ---------------------------------------------------------------------------

/// Нарушение структурного инварианта. Пустая книга — не нарушение: до первого
/// снапшота ей нечего накрывать, и это состояние легально.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvariantViolation {
    CrossedBook {
        best_bid_tick: i64,
        best_ask_tick: i64,
    },
    NonPositiveSize {
        side: Side,
        tick: i64,
        qty_lots: i64,
    },
    UnorderedLevels {
        side: Side,
    },
}

/// Инварианты шага 0.6: лучший бид строго меньше лучшего аска, размеры
/// положительны, порядок уровней сохранён. Пустой `Vec` — аллокаций нет.
pub fn check_invariants(book: &Book) -> Vec<InvariantViolation> {
    let mut out = Vec::new();
    if let (Some(b), Some(a)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
        if b >= a {
            out.push(InvariantViolation::CrossedBook {
                best_bid_tick: b,
                best_ask_tick: a,
            });
        }
    }
    for side in [Side::Bid, Side::Ask] {
        // `Book::levels` идёт от лучшей цены вглубь для обеих сторон:
        // биды по убыванию тика, аски по возрастанию.
        let mut prev: Option<i64> = None;
        for (tick, qty) in book.levels(side) {
            if qty <= 0 {
                out.push(InvariantViolation::NonPositiveSize {
                    side,
                    tick,
                    qty_lots: qty,
                });
            }
            if let Some(p) = prev {
                let ordered = match side {
                    Side::Bid => tick < p,
                    Side::Ask => tick > p,
                };
                if !ordered {
                    out.push(InvariantViolation::UnorderedLevels { side });
                    break;
                }
            }
            prev = Some(tick);
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Проверка 3 (ревизия 17а): сделка внутри диапазона на удерживаемой цене
// ---------------------------------------------------------------------------

/// Диапазон, накрытый книгой: от худшего удерживаемого бида до худшего
/// удерживаемого аска. `None` — книга пуста, судить не по чему.
pub fn book_span_ticks(book: &Book) -> Option<(i64, i64)> {
    let min_bid = book.levels(Side::Bid).map(|(t, _)| t).min()?;
    let max_ask = book.levels(Side::Ask).map(|(t, _)| t).max()?;
    Some((min_bid, max_ask))
}

/// Цена сделки против диапазона книги. `None` — книга пуста (или однобока:
/// спан не строится): это не нарушение, а неопределённость, и счётчик у неё
/// отдельный.
pub fn trade_in_range(book: &Book, price_tick: i64) -> Option<bool> {
    book_span_ticks(book).map(|(lo, hi)| price_tick >= lo && price_tick <= hi)
}

/// Цена удерживается книгой, если тик держит хотя бы одна сторона.
/// Ревизия 17б: для нарушения теста 3 этого мало — в L2 между уровнями бывают
/// пустые тики, и сделка по такому тику в момент, когда уровень уже съеден, —
/// норма, а не брак. Нарушение — цена, которую книга НИ РАЗУ не держала.
pub fn price_held(book: &Book, price_tick: i64) -> bool {
    book.qty_lots_at(Side::Bid, price_tick) != 0 || book.qty_lots_at(Side::Ask, price_tick) != 0
}

// ---------------------------------------------------------------------------
// Verifier: книга + счётчики трёх проверок в одном месте
// ---------------------------------------------------------------------------

/// Ошибка выравнивания для проверки 1: без совпадения ключа сравнение нельзя
/// читать как «книги расходятся» — это другая неисправность. `NotAligned`
/// (по `u`) оставлен для совместимости; живой ключ — `seq` (`NotAlignedSeq`):
/// `u` в WS и REST — два разных счётчика и выровняться не могут никогда.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AlignmentError {
    NotAligned {
        book_u: Option<u64>,
        snapshot_u: u64,
    },
    NotAlignedSeq {
        book_seq: Option<u64>,
        snapshot_seq: u64,
    },
}

/// Счётчики прогона. Только целые: доли считаются вызывающим в ppm
/// (`count * 1_000_000 / total`). Ревизия 17а: порог 0.1% из плана (1000 ppm)
/// относится к нарушениям; `out_of_range` — доля без порога.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct VerifyStats {
    pub updates_applied: u64,
    pub sequence_gaps: u64,
    pub invariant_violations: u64,
    pub trades_total: u64,
    pub trades_out_of_range: u64,
    pub trades_violations: u64,
    pub trades_indeterminate: u64,
    /// Из `trades_violations` — сколько пришлось на блочные сделки (`BT`):
    /// они по определению не потребляют видимую ликвидность и печатаются по
    /// договорной цене, поэтому попадание в тест 3 у них ожидаемо.
    pub violations_block: u64,
    /// Из `trades_violations` — сколько пришлось на RPI-сделки (исполнение об
    /// невидимую заявку). Флаг есть только с 2026-09-15T22:47:56Z.
    pub violations_rpi: u64,
    /// Из `trades_violations` — сколько пришлось на цены **строго внутри
    /// спреда**: там заявки стоять не может, и сделка означает исполнение об
    /// невидимое или проскочившее между апдейтами.
    pub violations_inside_spread: u64,
    /// Близость уровня на **той стороне**, которую сделка ела: ровно один тик.
    pub violations_adjacent: u64,
    /// Близость уровня на нужной стороне: два тика и дальше.
    pub violations_far: u64,
    /// У нужной стороны уровней нет вовсе (однобокая книга).
    pub violations_no_side: u64,
    /// Сколько прошло с последнего применённого обновления книги: 20–100 мс.
    pub violations_stale_20ms: u64,
    /// То же: 100 мс и больше.
    pub violations_stale_100ms: u64,
}

impl VerifyStats {
    /// Нарушения теста 3 (ревизия 17б: внутри диапазона на цене, которую книга
    /// ни разу не держала за время покрытия) в миллионных долях. `None` — сделок не было.
    pub fn trade_violation_ppm(&self) -> Option<u64> {
        if self.trades_total == 0 {
            return None;
        }
        Some(self.trades_violations * 1_000_000 / self.trades_total)
    }

    /// Доля сделок за границей видимой книги. Порога нет, это свойство глубины.
    /// `None` — сделок не было.
    pub fn out_of_range_ppm(&self) -> Option<u64> {
        if self.trades_total == 0 {
            return None;
        }
        Some(self.trades_out_of_range * 1_000_000 / self.trades_total)
    }
}

/// Книга с припаянными к ней счётчиками проверок 2-3 и точкой входа проверки 1.
/// Разрыв последовательности считает и дальше не применяет: книга после разрыва
/// недоверена, и это состояние видно в статистике, а не только в возврате.
pub struct Verifier {
    book: Book,
    stats: VerifyStats,
    /// Каждый тик, который книга держала хоть раз за время покрытия (ревизия
    /// 17б). Только вставки и проверки вхождения — порядок обхода не влияет
    /// ни на что, детерминизм A2 не задет. Память — тысячи distinct тиков.
    ever_held: std::collections::HashSet<i64>,
    /// Метка биржи последнего применённого обновления, мс. Нужна, чтобы отличать
    /// «книга не успела» (окно троттлинга) от «книга видела, но не то».
    last_update_ms: i64,
}

impl Verifier {
    pub fn new(tick_e9: i64, step_e9: i64) -> Self {
        Self {
            book: Book::new(tick_e9, step_e9),
            stats: VerifyStats::default(),
            ever_held: std::collections::HashSet::new(),
            last_update_ms: 0,
        }
    }

    pub fn book(&self) -> &Book {
        &self.book
    }

    pub fn stats(&self) -> VerifyStats {
        self.stats
    }

    /// Применяет обновление и сразу проверяет инварианты. Разрыв и пересечение
    /// книги — это `Err`, и счётчик растёт в обоих случаях: молчаливого пути нет.
    pub fn apply_update(
        &mut self,
        up: &Update,
    ) -> Result<Vec<InvariantViolation>, crate::book::ApplyError> {
        match self.book.apply(up) {
            Ok(()) => {
                self.stats.updates_applied += 1;
                self.last_update_ms = self.last_update_ms.max(up.cts_ms);
                // Все удерживаемые тики — в историю покрытия (ревизия 17б).
                // Пустой срез уровней невозможен: apply с нулевыми размерами
                // уровни удаляет, а не хранит.
                for side in [Side::Bid, Side::Ask] {
                    for (tick, _) in self.book.levels(side) {
                        self.ever_held.insert(tick);
                    }
                }
                let v = check_invariants(&self.book);
                self.stats.invariant_violations += v.len() as u64;
                Ok(v)
            }
            Err(e) => {
                match e {
                    crate::book::ApplyError::SequenceGap { .. } => {
                        self.stats.sequence_gaps += 1;
                    }
                    crate::book::ApplyError::Crossed { .. } => {
                        self.stats.invariant_violations += 1;
                    }
                    crate::book::ApplyError::PriceNotOnTick { .. }
                    | crate::book::ApplyError::QtyNotOnStep { .. } => {
                        self.stats.invariant_violations += 1;
                    }
                }
                Err(e)
            }
        }
    }

    /// Отмечает сделку ленты в проверке 3 (ревизия 17б, три исхода):
    /// пустая книга — indeterminate; вне диапазона — `out_of_range` без порога;
    /// внутри на цене, которую книга НИ РАЗУ не держала за время покрытия, —
    /// нарушение. Цена, удерживаемая сейчас или державшаяся раньше (пустой тик
    /// между уровнями, съеденный уровень), — не нарушение.
    pub fn observe_trade(
        &mut self,
        price_tick: i64,
        exch_ms: i64,
        block: bool,
        rpi: bool,
        aggressor_is_buy: bool,
    ) {
        self.stats.trades_total += 1;
        match trade_in_range(&self.book, price_tick) {
            None => self.stats.trades_indeterminate += 1,
            Some(false) => self.stats.trades_out_of_range += 1,
            Some(true) => {
                if !self.ever_held.contains(&price_tick) {
                    self.stats.trades_violations += 1;
                    if block {
                        self.stats.violations_block += 1;
                    }
                    if rpi {
                        self.stats.violations_rpi += 1;
                    }
                    if self.inside_spread(price_tick) {
                        self.stats.violations_inside_spread += 1;
                    }
                    self.classify_violation(price_tick, exch_ms, aggressor_is_buy);
                }
            }
        }
    }

    /// Чем ещё объясняется нарушение: близостью уровня на стороне, которую
    /// сделка ела, и давностью последнего обновления книги. Нужно, чтобы вопрос
    /// «это порча, задержка или разрешение наблюдения» имел числовой ответ.
    fn classify_violation(&mut self, price_tick: i64, exch_ms: i64, aggressor_is_buy: bool) {
        let side = if aggressor_is_buy {
            Side::Ask
        } else {
            Side::Bid
        };
        match self
            .book
            .levels(side)
            .map(|(t, _)| (t - price_tick).abs())
            .min()
        {
            Some(1) => self.stats.violations_adjacent += 1,
            Some(_) => self.stats.violations_far += 1,
            None => self.stats.violations_no_side += 1,
        }
        if self.last_update_ms > 0 {
            let dt = exch_ms - self.last_update_ms;
            if dt >= 100 {
                self.stats.violations_stale_100ms += 1;
            } else if dt >= 20 {
                self.stats.violations_stale_20ms += 1;
            }
        }
    }

    /// Цена строго между лучшим бидом и лучшим аском. Заявки там стоять не
    /// может по определению спреда, поэтому сделка по такому тику — исполнение
    /// об невидимое (RPI) или проскочившее между апдейтами, а не признак битой
    /// книги; счётчик нужен, чтобы это было видно числом.
    fn inside_spread(&self, price_tick: i64) -> bool {
        let bid = self.book.levels(Side::Bid).map(|(t, _)| t).max();
        let ask = self.book.levels(Side::Ask).map(|(t, _)| t).min();
        matches!((bid, ask), (Some(b), Some(a)) if price_tick > b && price_tick < a)
    }

    /// Проверка 1: книга обязана стоять ровно на `u` снапшота. Не стоит —
    /// `AlignmentError`, а не «грязный дифф»: смешивать эти два исхода запрещено.
    /// Оставлена для совместимости; живой ключ — `verify_at_seq`.
    pub fn verify_at_u(
        &self,
        snap: &OrderbookSnapshot,
        tick_e9: i64,
        step_e9: i64,
    ) -> Result<SnapshotDiff, AlignmentError> {
        if self.book.last_u() != Some(snap.u) {
            return Err(AlignmentError::NotAligned {
                book_u: self.book.last_u(),
                snapshot_u: snap.u,
            });
        }
        Ok(compare_with_snapshot(&self.book, snap, tick_e9, step_e9))
    }

    /// Проверка 1 по сквозному `seq`: книга обязана стоять ровно на `seq`
    /// снапшота. Живой ключ выравнивания (`u` в WS и REST — разные счётчики).
    pub fn verify_at_seq(
        &self,
        snap: &OrderbookSnapshot,
        tick_e9: i64,
        step_e9: i64,
    ) -> Result<SnapshotDiff, AlignmentError> {
        if self.book.last_seq() != Some(snap.seq) {
            return Err(AlignmentError::NotAlignedSeq {
                book_seq: self.book.last_seq(),
                snapshot_seq: snap.seq,
            });
        }
        Ok(compare_with_snapshot(&self.book, snap, tick_e9, step_e9))
    }
}

// ---------------------------------------------------------------------------
// Файловый реплей: записи суток -> Updates с синтетическими u (проверки 2-3)
// ---------------------------------------------------------------------------

/// Сделка, извлечённая из записи файла: тик цены и флаги.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradePoint {
    pub tick: i64,
    pub block: bool,
    /// Исполнение об RPI-заявку (`Record.rpi`): флаг есть только в файлах с
    /// 2026-09-15T22:47:56Z, раньше — «не размечено».
    pub rpi: bool,
    /// Метка биржи, мс (`exch_ts_ns / 1e6`): по ней считается, сколько прошло с
    /// последнего применённого обновления книги.
    pub exch_ms: i64,
    /// Агрессор-покупатель: он ест аск, значит уровень искать на стороне асков.
    pub aggressor_is_buy: bool,
}

fn is_snapshot_ev(ev: u64) -> bool {
    ev == LOCAL_BID_DEPTH_SNAPSHOT_EVENT || ev == LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
}

pub(crate) fn is_trade_ev(ev: u64) -> bool {
    ev == LOCAL_BUY_TRADE_EVENT || ev == LOCAL_SELL_TRADE_EVENT
}

fn depth_side(ev: u64) -> Option<Side> {
    match ev {
        LOCAL_BID_DEPTH_EVENT | LOCAL_BID_DEPTH_SNAPSHOT_EVENT => Some(Side::Bid),
        LOCAL_ASK_DEPTH_EVENT | LOCAL_ASK_DEPTH_SNAPSHOT_EVENT => Some(Side::Ask),
        _ => None,
    }
}

/// Группирует записи суточного файла в обновления книги и точки сделок.
/// Одно WS-сообщение — один `Update`: записи одного сообщения делят метку
/// `exch_ts_ns`, и граница группы проходит по её смене (плюс смена
/// снапшот/дельта и сделки, которые обновлениями не являются).
///
/// Граница по кадрам для группировки ничего не значит: кадр — транспортная
/// нарезка по числу записей, и одно сообщение обязано лежать в двух кадрах.
/// Группировка «по кадру» рвала сообщение пополам: бид-половина применялась
/// отдельным обновлением, книга transiently пересекалась, и реплей живого
/// 5-минутного файла вставал на 12-м кадре с ложным `Crossed` — при том что
/// рекордер применяет сообщение всегда целиком (поймано прогоном 3.1).
/// Поэтому конвертер — структура с состоянием (`FileReplayer`), а не функция
/// одного среза: незакрытая группа переживает границу кадра.
///
/// Счётчик синтетических `u` — тоже сквозной на весь файл: заведённый внутри
/// вызова, он начинал бы каждый кадр заново (предыдущая итерация той же ошибки).
/// Снапшот идёт с `u = 1` по семантике рестарта `Book::apply` и сбрасывает
/// счётчик на 2 — новая эпоха, как вживую.
///
/// Синтетика `u` годится для проверок 2-3, но НЕ для проверки 1: файл `u` не
/// хранит, и «проверка 1 на файле» была бы сравнением с выдуманным выравниванием.
pub struct FileReplayer {
    bids: Vec<(i64, i64)>,
    asks: Vec<(i64, i64)>,
    cur_snapshot: bool,
    cur_ts_ns: i64,
    has_open: bool,
    next_u: u64,
}

impl FileReplayer {
    pub fn new() -> Self {
        Self {
            bids: Vec::new(),
            asks: Vec::new(),
            cur_snapshot: false,
            cur_ts_ns: 0,
            has_open: false,
            next_u: 2,
        }
    }

    fn flush(&mut self, updates: &mut Vec<Update>) {
        if !self.has_open {
            return;
        }
        let u = if self.cur_snapshot { 1 } else { self.next_u };
        if !self.cur_snapshot {
            self.next_u += 1;
        }
        // Файл `seq` не хранит: синтетика зеркалит `u` (проверки 2-3, не 1).
        // Глубина — потока основного файла (T45): реплей читает
        // `<root>/<SYMBOL>-<день>.binlog`, глубокий файл лежит отдельно и
        // этого признака в формате не несёт (v3 заморожен, В-49).
        updates.push(Update {
            is_snapshot: self.cur_snapshot,
            depth: ORDERBOOK_DEPTH,
            u,
            seq: u,
            cts_ms: self.cur_ts_ns / 1_000_000,
            bids: std::mem::take(&mut self.bids),
            asks: std::mem::take(&mut self.asks),
        });
        self.has_open = false;
    }

    /// Принимает записи очередного кадра. Возвращает закрывшиеся обновления
    /// и точки сделок; незакрытая группа (одно сообщение, разрезанное границей
    /// кадра) остаётся внутри и допишется следующим кадром.
    pub fn push_frame(
        &mut self,
        records: &[Record],
        tick_e9: i64,
        step_e9: i64,
        updates: &mut Vec<Update>,
        trades: &mut Vec<TradePoint>,
    ) {
        for r in records {
            if is_trade_ev(r.ev) {
                self.flush(updates);
                trades.push(TradePoint {
                    tick: r.price_ticks,
                    block: r.block,
                    rpi: r.rpi,
                    exch_ms: r.exch_ts_ns / 1_000_000,
                    aggressor_is_buy: r.ev == LOCAL_BUY_TRADE_EVENT,
                });
                continue;
            }
            let Some(side) = depth_side(r.ev) else {
                continue;
            };
            let snap = is_snapshot_ev(r.ev);
            // Новое сообщение — новая группа: та же метка времени и тот же
            // вид кадра продолжают группу, всё остальное её закрывает.
            // Два разных сообщения с одной меткой (та же миллисекунда) честно
            // сливаются: порядок внутри миллисекунды всё равно неразличим, а
            // атомарность спасает от ложного пересечения.
            if self.has_open && (snap != self.cur_snapshot || r.exch_ts_ns != self.cur_ts_ns) {
                self.flush(updates);
            }
            if snap {
                self.next_u = 2;
            }
            if !self.has_open {
                self.cur_snapshot = snap;
                self.cur_ts_ns = r.exch_ts_ns;
                self.has_open = true;
            }
            let qty_e9 = r.qty_lots * step_e9;
            let px_e9 = r.price_ticks * tick_e9;
            match side {
                Side::Bid => self.bids.push((px_e9, qty_e9)),
                Side::Ask => self.asks.push((px_e9, qty_e9)),
            }
        }
    }

    /// Закрывает остаток потока. Вызывать один раз в конце файла.
    pub fn finish(&mut self, updates: &mut Vec<Update>) {
        self.flush(updates);
    }
}

impl Default for FileReplayer {
    fn default() -> Self {
        Self::new()
    }
}

/// Одноразовая обёртка над `FileReplayer` для тестов и простых случаев:
/// весь срез — один поток, счётчик заводится свежим.
pub fn records_to_updates(
    records: &[Record],
    tick_e9: i64,
    step_e9: i64,
    next_u: &mut u64,
) -> (Vec<Update>, Vec<TradePoint>) {
    let mut rp = FileReplayer {
        bids: Vec::new(),
        asks: Vec::new(),
        cur_snapshot: false,
        cur_ts_ns: 0,
        has_open: false,
        next_u: *next_u,
    };
    let mut updates = Vec::new();
    let mut trades = Vec::new();
    rp.push_frame(records, tick_e9, step_e9, &mut updates, &mut trades);
    rp.finish(&mut updates);
    *next_u = rp.next_u;
    (updates, trades)
}

// ---------------------------------------------------------------------------
// CLI: lob verify --symbol S --root data/bybit (файловый режим, проверки 2-3)
// ---------------------------------------------------------------------------

/// Аргументы подкоманды `verify`. Живого режима здесь нет: ему нужен сокетный
/// контур записи, которого в этом проходе нет, — живой путь открыт как API
/// `Verifier` и покрыт `#[ignore]`-тестом на настоящем REST.
#[derive(Debug, Clone, clap::Args)]
pub struct VerifyArgs {
    /// Символ, например BTCUSDT.
    #[arg(long)]
    pub symbol: String,
    /// Корень записи: суточные файлы и заголовки (`tickSize`/`qtyStep`).
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
}

/// Итог файлового прогона для печати и `VerifyStats` вызывающему.
/// Ревизия 17б: `trades_out_of_range` — доля без порога, `trades_violations` —
/// нарушения теста 3 (цена ни разу не держалась) с порогом < 0.1 % сделок
/// (план §11); порог применяет вердикт — `commands::lob::verify::VerifyStatus::of`
/// (В-56), здесь только счётчики.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct VerifySummary {
    pub files: usize,
    pub updates_applied: u64,
    pub sequence_gaps: u64,
    pub invariant_violations: u64,
    pub trades_total: u64,
    pub trades_out_of_range: u64,
    pub trades_violations: u64,
    pub trades_indeterminate: u64,
    pub violations_block: u64,
    pub violations_rpi: u64,
    pub violations_inside_spread: u64,
    pub violations_adjacent: u64,
    pub violations_far: u64,
    pub violations_no_side: u64,
    pub violations_stale_20ms: u64,
    pub violations_stale_100ms: u64,
}

impl VerifySummary {
    /// Нарушения теста 3 в ppm. `None` — сделок не было.
    pub fn violation_ppm(&self) -> Option<u64> {
        if self.trades_total == 0 {
            return None;
        }
        Some(self.trades_violations * 1_000_000 / self.trades_total)
    }

    /// Доля вне диапазона в ppm (порога нет). `None` — сделок не было.
    pub fn out_of_range_ppm(&self) -> Option<u64> {
        if self.trades_total == 0 {
            return None;
        }
        Some(self.trades_out_of_range * 1_000_000 / self.trades_total)
    }
}

/// Хронологический ключ файла `<SYMBOL>-<день>[-pN].binlog[.zst]`: день,
/// потом часть суток. Правило — `binlog::binlog_file_order_key` (одно на все
/// слои; `bybit` не зависит от `commands`, граница слоёв, поэтому общее
/// правило живёт в `binlog` — ниже обоих).
fn file_order_key(prefix: &str, name: &str) -> (String, u32) {
    crate::binlog::binlog_file_order_key(prefix, name)
}

/// Прогоняет все суточные файлы символа через проверки 2-3. Проверка 1 здесь
/// невозможна по построению (у файла нет `u`) и не выполняется молча как
/// «чисто»: она отсутствует в отчёте вообще, а не со значением ноль.
pub fn run_verify(args: &VerifyArgs) -> anyhow::Result<VerifySummary> {
    let mut files: Vec<PathBuf> = Vec::new();
    let prefix = format!("{}-", args.symbol);
    let entries = std::fs::read_dir(&args.root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", args.root.display()))?;
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога: {e}"))?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(&prefix) && crate::binlog::is_binlog_file_name(&name) {
            files.push(e.path());
        }
    }
    files.sort_by(|a, b| {
        let key = |p: &PathBuf| {
            p.file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        };
        file_order_key(&prefix, &key(a)).cmp(&file_order_key(&prefix, &key(b)))
    });
    // Оригинал и его архив одной части — одни сутки (T46): сверять их дважды
    // значило бы удвоить `updates`/`trades` в сводке каталога. Побеждает
    // обычный файл (`binlog::dedupe_same_day_part`).
    crate::binlog::dedupe_same_day_part(&prefix, &mut files);
    if files.is_empty() {
        // Таск 19, тот же приём, что `commands::lob::mod::
        // replay_symbol_over_configs`/`session_binlog_for`: каталог
        // старого формата (до таска 19 `lob session` писала
        // `<SYMBOL>.binlog` без даты) не должен выглядеть как «нет
        // суточных файлов» — владелец переименовывает руками, но обязан
        // узнать об этом из сообщения, не из тишины. Резолвер `commands::
        // lob` сюда не завозится (`bybit` не зависит от `commands` —
        // граница слоёв), поэтому проверка своя, тем же текстом ошибки;
        // архивный суффикс (T46) проверяется так же, как обычный.
        for suffix in [
            crate::binlog::BINLOG_SUFFIX,
            crate::binlog::BINLOG_ARCHIVE_SUFFIX,
        ] {
            let name = format!("{}{suffix}", args.symbol);
            let undated = args.root.join(&name);
            if undated.is_file() {
                anyhow::bail!(
                    "файл `{name}` без даты — запись старого формата, переименуйте в \
                     `{}-<дата>{suffix}` ({} в {})",
                    args.symbol,
                    undated.display(),
                    args.root.display()
                );
            }
        }
        anyhow::bail!(
            "нет суточных файлов {}-*.binlog в {}",
            args.symbol,
            args.root.display()
        );
    }
    let mut summary = VerifySummary::default();
    for path in &files {
        verify_one_file(path, &mut summary)?;
    }
    summary.files = files.len();
    Ok(summary)
}

/// Проверки 2-3 по **одному** файлу (части записи, таск 22) — `files = 1`.
/// Вход для читателей, которые сами нашли части символа своим резолвером
/// (`commands::lob::verify::verify_and_mark` через `session_binlog_for`,
/// таск 26) и хотят сводку на каждую часть, не сумму по каталогу;
/// `run_verify` выше — тот же `verify_one_file`, только по своему обходу.
pub fn verify_file(path: &Path) -> anyhow::Result<VerifySummary> {
    let mut summary = VerifySummary::default();
    verify_one_file(path, &mut summary)?;
    summary.files = 1;
    Ok(summary)
}

fn verify_one_file(path: &Path, summary: &mut VerifySummary) -> anyhow::Result<()> {
    let data = std::fs::read(path)
        .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
    let mut reader = crate::binlog::Reader::open(&data[..])
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    let mut verifier = Verifier::new(header.tick_e9, header.step_e9);
    // Один конвертер на весь файл: сообщение обязано лежать в двух кадрах,
    // и незакрытая группа переживает границу кадра внутри него.
    let mut replayer = FileReplayer::new();
    loop {
        // Мягкий вариант (A4, 2026-09-17): `lob verify` читает в том числе
        // живой корень, а запись кладёт кадр не одним `write` — обрезанный
        // **хвостовой** кадр здесь означает «файл ещё пишется», а не порчу.
        let frame = reader
            .read_frame_soft()
            .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
        if frame.is_none() && reader.truncated_tail() {
            eprintln!(
                "verify: {} — хвостовой кадр обрезан (файл дописывается), сверяю прочитанное",
                path.display()
            );
        }
        let Some(records) = frame else { break };
        let mut updates = Vec::new();
        let mut trades = Vec::new();
        replayer.push_frame(
            &records,
            header.tick_e9,
            header.step_e9,
            &mut updates,
            &mut trades,
        );
        for up in &updates {
            // Разрыв в файловом реплее означает битый файл, а не рынок:
            // дальше этот файл не идёт, следующий — с чистого Verifier.
            if verifier.apply_update(up).is_err() {
                let s = verifier.stats();
                summary.updates_applied += s.updates_applied;
                summary.sequence_gaps += s.sequence_gaps;
                summary.invariant_violations += s.invariant_violations;
                summary.trades_total += s.trades_total;
                summary.trades_out_of_range += s.trades_out_of_range;
                summary.trades_violations += s.trades_violations;
                summary.trades_indeterminate += s.trades_indeterminate;
                summary.violations_block += s.violations_block;
                summary.violations_rpi += s.violations_rpi;
                summary.violations_inside_spread += s.violations_inside_spread;
                summary.violations_adjacent += s.violations_adjacent;
                summary.violations_far += s.violations_far;
                summary.violations_no_side += s.violations_no_side;
                summary.violations_stale_20ms += s.violations_stale_20ms;
                summary.violations_stale_100ms += s.violations_stale_100ms;
                return Ok(());
            }
        }
        for t in &trades {
            verifier.observe_trade(t.tick, t.exch_ms, t.block, t.rpi, t.aggressor_is_buy);
        }
    }
    // Хвост файла: сообщение, закрывшееся концом потока, а не следующим.
    let mut tail = Vec::new();
    replayer.finish(&mut tail);
    for up in &tail {
        if verifier.apply_update(up).is_err() {
            break;
        }
    }
    let s = verifier.stats();
    summary.updates_applied += s.updates_applied;
    summary.sequence_gaps += s.sequence_gaps;
    summary.invariant_violations += s.invariant_violations;
    summary.trades_total += s.trades_total;
    summary.trades_out_of_range += s.trades_out_of_range;
    summary.trades_violations += s.trades_violations;
    summary.trades_indeterminate += s.trades_indeterminate;
    summary.violations_block += s.violations_block;
    summary.violations_rpi += s.violations_rpi;
    summary.violations_inside_spread += s.violations_inside_spread;
    summary.violations_adjacent += s.violations_adjacent;
    summary.violations_far += s.violations_far;
    summary.violations_no_side += s.violations_no_side;
    summary.violations_stale_20ms += s.violations_stale_20ms;
    summary.violations_stale_100ms += s.violations_stale_100ms;
    Ok(())
}

// ---------------------------------------------------------------------------
// Тесты
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests;
