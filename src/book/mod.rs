//! Реконструкция L2-книги.
//!
//! Одна реализация на все режимы: рекордер, реплей, разметка. Цены и размеры —
//! целые (`ARCHITECTURE.md` A1), потому что сравнение уровней по цене происходит
//! миллионы раз в сутки и уровень-призрак, родившийся из представления, попадает
//! в выборку неотличимо от настоящего.

use std::cmp::Ordering;

/// Сторона книги.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Bid,
    Ask,
}

/// Ошибка применения обновления. Все варианты означают «книга больше не доверена»
/// и требуют ресинка снапшотом, а не частичной починки.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApplyError {
    /// Разрыв в `u`: пришло обновление, не продолжающее предыдущее.
    /// Несёт обе величины, потому что в логе разрыва нужен размер, а не факт.
    SequenceGap { expected: u64, got: u64 },
    /// Цена не кратна `tick_size`, записанному в заголовке. Значит биржа сменила
    /// шаг посреди записи, и дельты в тиках поехали бы по масштабу молча.
    PriceNotOnTick { price_e9: i64, tick_e9: i64 },
    /// То же для количества.
    QtyNotOnStep { qty_e9: i64, step_e9: i64 },
    /// Ask не выше bid после применения — книга пересеклась.
    Crossed {
        best_bid_tick: i64,
        best_ask_tick: i64,
    },
}

/// Одна сторона книги: отсортированный массив пар (тик, количество).
///
/// Не `BTreeMap`: он аллоцирует на вставку уровня и гоняет указатели на каждом
/// обходе, а гейт GC требует ноль аллокаций на событие. Не кольцевой буфер по
/// смещению от опорной цены, как предполагал черновик архитектуры: при глубине в
/// пятьдесят уровней memmove по массиву дешевле, чем поддержание опорной точки и
/// её переустановка, а выигрыш кольца появляется на глубине совсем другого порядка.
#[derive(Debug, Clone)]
struct HalfBook {
    /// Отсортирован по возрастанию тика — для обеих сторон, чтобы `binary_search`
    /// был один. Лучшая цена у бидов в конце, у асков в начале.
    levels: Vec<(i64, i64)>,
}

impl HalfBook {
    fn with_capacity(cap: usize) -> Self {
        Self {
            levels: Vec::with_capacity(cap),
        }
    }

    /// Ставит количество на тик. Ноль удаляет уровень — так Bybit сообщает,
    /// что «все котировки по этой цене исполнены или сняты».
    ///
    /// Аллокаций нет, пока не превышена ёмкость: `insert` в `Vec` — это memmove.
    ///
    /// Индексы из `binary_search_by` доказаны самим поиском (`Ok(i)` — внутри),
    /// проверка через `get` в горячем пути стоила бы ветвление на каждое
    /// событие книги (гейт GC меряет именно этот путь).
    #[allow(clippy::indexing_slicing)]
    fn set(&mut self, tick: i64, qty: i64) {
        match self.levels.binary_search_by(|probe| probe.0.cmp(&tick)) {
            Ok(i) => {
                if qty == 0 {
                    self.levels.remove(i);
                } else {
                    self.levels[i].1 = qty;
                }
            }
            Err(i) => {
                if qty != 0 {
                    self.levels.insert(i, (tick, qty));
                }
            }
        }
    }

    #[allow(clippy::indexing_slicing)]
    fn qty_at(&self, tick: i64) -> i64 {
        match self.levels.binary_search_by(|probe| probe.0.cmp(&tick)) {
            Ok(i) => self.levels[i].1,
            Err(_) => 0,
        }
    }

    fn clear(&mut self) {
        self.levels.clear();
    }

    fn len(&self) -> usize {
        self.levels.len()
    }
}

/// Максимум уровней на сторону, который мы держим. Подписка `orderbook.50` даёт
/// пятьдесят; запас нужен потому, что дельта может назвать цену, вышедшую за топ,
/// и мы обязаны её удалить, а не проигнорировать.
const CAPACITY: usize = 128;

/// Книга по одному инструменту.
#[derive(Debug, Clone)]
pub struct Book {
    bids: HalfBook,
    asks: HalfBook,
    /// Шаг цены и шаг количества в единицах 1e-9. Целые, потому что кратность
    /// проверяется точно, а `f64` дал бы ложные срабатывания на самом же шаге.
    tick_e9: i64,
    step_e9: i64,
    /// `u` последнего применённого обновления. `None` — книга пуста и ждёт снапшот.
    last_u: Option<u64>,
    /// `seq` последнего применённого обновления (WS `seq` / REST `seq` — один
    /// счётчик в обоих каналах, в отличие от `u`). Хранится рядом с `last_u`,
    /// в sequence-контроле НЕ участвует: поток по-прежнему ведётся по `u`.
    last_seq: Option<u64>,
    /// `cts` последнего обновления: время матчинга, ключ склейки с лентой сделок.
    /// У `publicTrade` своего `cts` нет, там время матчинга называется `T`.
    last_cts_ms: i64,
}

/// Одно обновление стакана, уже разобранное из JSON.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Update {
    pub is_snapshot: bool,
    pub u: u64,
    /// Сквозной `seq` Bybit (WS `data.seq` и REST `result.seq` — один счётчик).
    /// В контроле потока не участвует, только для выравнивания сверки.
    pub seq: u64,
    pub cts_ms: i64,
    /// Пары (цена в 1e-9, количество в 1e-9).
    pub bids: Vec<(i64, i64)>,
    pub asks: Vec<(i64, i64)>,
}

impl Book {
    pub fn new(tick_e9: i64, step_e9: i64) -> Self {
        assert!(tick_e9 > 0 && step_e9 > 0, "шаги должны быть положительны");
        Self {
            bids: HalfBook::with_capacity(CAPACITY),
            asks: HalfBook::with_capacity(CAPACITY),
            tick_e9,
            step_e9,
            last_u: None,
            last_seq: None,
            last_cts_ms: 0,
        }
    }

    pub fn is_synced(&self) -> bool {
        self.last_u.is_some()
    }

    pub fn last_u(&self) -> Option<u64> {
        self.last_u
    }

    pub fn last_seq(&self) -> Option<u64> {
        self.last_seq
    }

    pub fn last_cts_ms(&self) -> i64 {
        self.last_cts_ms
    }

    fn to_tick(&self, price_e9: i64) -> Result<i64, ApplyError> {
        if price_e9 % self.tick_e9 != 0 {
            return Err(ApplyError::PriceNotOnTick {
                price_e9,
                tick_e9: self.tick_e9,
            });
        }
        Ok(price_e9 / self.tick_e9)
    }

    fn to_lots(&self, qty_e9: i64) -> Result<i64, ApplyError> {
        if qty_e9 % self.step_e9 != 0 {
            return Err(ApplyError::QtyNotOnStep {
                qty_e9,
                step_e9: self.step_e9,
            });
        }
        Ok(qty_e9 / self.step_e9)
    }

    /// Применяет обновление.
    ///
    /// Три правила Bybit, каждое из которых при нарушении портит выборку молча:
    /// `u = 1` означает рестарт сервиса и полную перезапись книги; разрыв `u`
    /// означает потерянное сообщение и требует ресинка; `size = 0` удаляет уровень.
    pub fn apply(&mut self, up: &Update) -> Result<(), ApplyError> {
        // Рестарт сервиса. Bybit присылает `u = 1` снапшотом, и локальную книгу
        // нужно перезаписать целиком, а не продолжать — иначе в ней навсегда
        // останутся уровни, которых на бирже уже нет.
        let restart = up.u == 1;

        if up.is_snapshot || restart {
            self.bids.clear();
            self.asks.clear();
        } else {
            match self.last_u {
                None => {
                    // Дельта до снапшота. Не ошибка потока, но применять нечего.
                    return Err(ApplyError::SequenceGap {
                        expected: 0,
                        got: up.u,
                    });
                }
                Some(prev) if up.u <= prev => {
                    // Повтор или перестановка. Bybit гарантирует возрастание `u`,
                    // поэтому это тоже разрыв, а не безобидный дубль.
                    return Err(ApplyError::SequenceGap {
                        expected: prev + 1,
                        got: up.u,
                    });
                }
                Some(prev) if up.u != prev + 1 => {
                    return Err(ApplyError::SequenceGap {
                        expected: prev + 1,
                        got: up.u,
                    });
                }
                Some(_) => {}
            }
        }

        for &(px, qty) in &up.bids {
            let tick = self.to_tick(px)?;
            let lots = self.to_lots(qty)?;
            self.bids.set(tick, lots);
        }
        for &(px, qty) in &up.asks {
            let tick = self.to_tick(px)?;
            let lots = self.to_lots(qty)?;
            self.asks.set(tick, lots);
        }

        if let (Some(b), Some(a)) = (self.best_bid_tick_opt(), self.best_ask_tick_opt()) {
            if b >= a {
                return Err(ApplyError::Crossed {
                    best_bid_tick: b,
                    best_ask_tick: a,
                });
            }
        }

        self.last_u = Some(up.u);
        self.last_seq = Some(up.seq);
        self.last_cts_ms = up.cts_ms;
        Ok(())
    }

    pub fn best_bid_tick_opt(&self) -> Option<i64> {
        self.bids.levels.last().map(|l| l.0)
    }

    pub fn best_ask_tick_opt(&self) -> Option<i64> {
        self.asks.levels.first().map(|l| l.0)
    }

    pub fn depth(&self, side: Side) -> usize {
        match side {
            Side::Bid => self.bids.len(),
            Side::Ask => self.asks.len(),
        }
    }

    /// Количество в лотах на тике. Целое — в отличие от `MarketDepth`, который
    /// требует `f64` на границе с крейтом.
    pub fn qty_lots_at(&self, side: Side, tick: i64) -> i64 {
        match side {
            Side::Bid => self.bids.qty_at(tick),
            Side::Ask => self.asks.qty_at(tick),
        }
    }

    /// Масштабы для границы A7 ниже: та же конверсия, то же обоснование
    /// (шаги — целые 1e-9 порядков единиц–миллиардов, точны в f64).
    #[allow(clippy::cast_precision_loss)]
    fn tick_size_f(&self) -> f64 {
        self.tick_e9 as f64 / 1e9
    }

    #[allow(clippy::cast_precision_loss)]
    fn lot_size_f(&self) -> f64 {
        self.step_e9 as f64 / 1e9
    }
}

/// Наша книга реализует трейт **крейта**, а не свой (`ARCHITECTURE.md` A7).
///
/// Смысл ровно один: логика уровней и стратегия пишутся generic над `MarketDepth`
/// и потому работают в трёх местах без единой ветки — на реплее, где эта книга и
/// есть источник, и в `Backtest`/`LiveBot`, где источник приносит крейт. Свой трейт
/// книги означал бы адаптер, а адаптер — второе поведение, которое нечем проверить.
///
/// Здесь же единственное место, где целые превращаются в `f64`: трейт требует его,
/// и граница проходит по трейту, а не глубже. Касты точные в обе стороны
/// границы: тики и лоты — целые порядков единиц–миллионов (точны в f64),
/// а деление масштаба на 1e9 — сама требуемая конверсия, не потеря.
#[allow(clippy::cast_precision_loss)]
impl hftbacktest::depth::MarketDepth for Book {
    fn best_bid(&self) -> f64 {
        match self.best_bid_tick_opt() {
            Some(t) => t as f64 * self.tick_size_f(),
            None => f64::NEG_INFINITY,
        }
    }

    fn best_ask(&self) -> f64 {
        match self.best_ask_tick_opt() {
            Some(t) => t as f64 * self.tick_size_f(),
            None => f64::INFINITY,
        }
    }

    fn best_bid_tick(&self) -> i64 {
        self.best_bid_tick_opt()
            .unwrap_or(hftbacktest::depth::INVALID_MIN)
    }

    fn best_ask_tick(&self) -> i64 {
        self.best_ask_tick_opt()
            .unwrap_or(hftbacktest::depth::INVALID_MAX)
    }

    fn best_bid_qty(&self) -> f64 {
        match self.best_bid_tick_opt() {
            Some(t) => self.qty_lots_at(Side::Bid, t) as f64 * self.lot_size_f(),
            None => 0.0,
        }
    }

    fn best_ask_qty(&self) -> f64 {
        match self.best_ask_tick_opt() {
            Some(t) => self.qty_lots_at(Side::Ask, t) as f64 * self.lot_size_f(),
            None => 0.0,
        }
    }

    fn tick_size(&self) -> f64 {
        self.tick_size_f()
    }

    fn lot_size(&self) -> f64 {
        self.lot_size_f()
    }

    fn bid_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.qty_lots_at(Side::Bid, price_tick) as f64 * self.lot_size_f()
    }

    fn ask_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.qty_lots_at(Side::Ask, price_tick) as f64 * self.lot_size_f()
    }
}

impl PartialEq for Book {
    /// Равенство по содержимому, не по служебным полям: этим сравнивается книга
    /// реплея с книгой рекордера в тесте детерминизма.
    fn eq(&self, other: &Self) -> bool {
        self.tick_e9 == other.tick_e9
            && self.step_e9 == other.step_e9
            && self.bids.levels == other.bids.levels
            && self.asks.levels == other.asks.levels
    }
}

impl Eq for Book {}

/// Итератор по уровням от лучшей цены вглубь. Нужен разметке уровней и `verify`.
///
/// Индексы доказаны счётчиком (`i` идёт ровно по `0..n` длины своей стороны);
/// вариант через `rev()`/`Box<dyn>` стоил бы аллокацию или второй тип
/// итератора на каждый вызов горячего пути ради того же факта.
#[allow(clippy::indexing_slicing)]
impl Book {
    pub fn levels(&self, side: Side) -> impl Iterator<Item = (i64, i64)> + '_ {
        let bids = &self.bids.levels;
        let asks = &self.asks.levels;
        let n = match side {
            Side::Bid => bids.len(),
            Side::Ask => asks.len(),
        };
        (0..n).map(move |i| match side {
            Side::Bid => bids[bids.len() - 1 - i],
            Side::Ask => asks[i],
        })
    }
}

/// Сравнение цен как тиков — свободная функция, чтобы её можно было использовать
/// в разметке, не таща туда книгу.
pub fn cmp_ticks(a: i64, b: i64) -> Ordering {
    a.cmp(&b)
}

#[cfg(test)]
mod tests;
