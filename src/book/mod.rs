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

    fn tick_size_f(&self) -> f64 {
        self.tick_e9 as f64 / 1e9
    }

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
/// и граница проходит по трейту, а не глубже.
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
mod tests {
    use super::*;

    const TICK: i64 = 100_000; // 0.0001
    const STEP: i64 = 1_000_000; // 0.001

    fn px(v: f64) -> i64 {
        (v * 1e9).round() as i64
    }

    fn snapshot(u: u64, bids: &[(f64, f64)], asks: &[(f64, f64)]) -> Update {
        Update {
            is_snapshot: true,
            u,
            seq: u,
            cts_ms: 1_000,
            bids: bids.iter().map(|&(p, q)| (px(p), px(q))).collect(),
            asks: asks.iter().map(|&(p, q)| (px(p), px(q))).collect(),
        }
    }

    fn delta(u: u64, bids: &[(f64, f64)], asks: &[(f64, f64)]) -> Update {
        Update {
            is_snapshot: false,
            u,
            seq: u,
            cts_ms: 1_000 + u as i64,
            bids: bids.iter().map(|&(p, q)| (px(p), px(q))).collect(),
            asks: asks.iter().map(|&(p, q)| (px(p), px(q))).collect(),
        }
    }

    fn book() -> Book {
        Book::new(TICK, STEP)
    }

    #[test]
    fn snapshot_then_delta_applies() {
        let mut b = book();
        b.apply(&snapshot(
            10,
            &[(1.0000, 5.0), (0.9999, 3.0)],
            &[(1.0001, 4.0)],
        ))
        .unwrap();
        assert_eq!(b.depth(Side::Bid), 2);
        assert_eq!(b.depth(Side::Ask), 1);
        assert_eq!(b.best_bid_tick_opt(), Some(px(1.0000) / TICK));
        assert_eq!(b.best_ask_tick_opt(), Some(px(1.0001) / TICK));

        b.apply(&delta(11, &[(1.0000, 7.0)], &[])).unwrap();
        assert_eq!(b.qty_lots_at(Side::Bid, px(1.0000) / TICK), px(7.0) / STEP);
    }

    /// `size = 0` удаляет уровень. Без этого снятая плотность осталась бы в книге
    /// стоять вечно и попала бы в разметку как живая.
    #[test]
    fn zero_size_removes_the_level() {
        let mut b = book();
        b.apply(&snapshot(
            10,
            &[(1.0000, 5.0), (0.9999, 3.0)],
            &[(1.0001, 4.0)],
        ))
        .unwrap();
        b.apply(&delta(11, &[(0.9999, 0.0)], &[])).unwrap();

        assert_eq!(b.depth(Side::Bid), 1);
        assert_eq!(b.qty_lots_at(Side::Bid, px(0.9999) / TICK), 0);
    }

    /// Удаление цены, которой в книге нет, не должно её создавать.
    #[test]
    fn zero_size_on_absent_level_is_a_no_op() {
        let mut b = book();
        b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
            .unwrap();
        b.apply(&delta(11, &[(0.5000, 0.0)], &[])).unwrap();
        assert_eq!(b.depth(Side::Bid), 1);
    }

    /// Пропуск `u` обязан форсировать ресинк, а не молча продолжить: пропущенное
    /// сообщение — это уровни, которых в нашей книге либо нет, либо уже не должно быть.
    #[test]
    fn gap_in_u_is_reported() {
        let mut b = book();
        b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
            .unwrap();
        let err = b.apply(&delta(13, &[(1.0000, 6.0)], &[])).unwrap_err();
        assert_eq!(
            err,
            ApplyError::SequenceGap {
                expected: 11,
                got: 13
            }
        );
        // Книга не тронута: отказ должен быть атомарным на уровне решения,
        // иначе после разрыва в ней смесь двух состояний.
        assert_eq!(b.last_u(), Some(10));
        assert_eq!(b.qty_lots_at(Side::Bid, px(1.0000) / TICK), px(5.0) / STEP);
    }

    #[test]
    fn repeated_u_is_a_gap_too() {
        let mut b = book();
        b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
            .unwrap();
        b.apply(&delta(11, &[(1.0000, 6.0)], &[])).unwrap();
        let err = b.apply(&delta(11, &[(1.0000, 7.0)], &[])).unwrap_err();
        assert!(matches!(err, ApplyError::SequenceGap { .. }));
    }

    #[test]
    fn delta_before_any_snapshot_is_rejected() {
        let mut b = book();
        let err = b.apply(&delta(5, &[(1.0000, 5.0)], &[])).unwrap_err();
        assert_eq!(
            err,
            ApplyError::SequenceGap {
                expected: 0,
                got: 5
            }
        );
        assert!(!b.is_synced());
    }

    /// `u = 1` посреди потока — рестарт сервиса Bybit. Книга перезаписывается
    /// целиком; уровни, пришедшие до рестарта, не выживают.
    #[test]
    fn u_equals_one_overwrites_the_whole_book() {
        let mut b = book();
        b.apply(&snapshot(
            10,
            &[(1.0000, 5.0), (0.9999, 3.0)],
            &[(1.0001, 4.0)],
        ))
        .unwrap();

        let mut restart = delta(1, &[(0.5000, 2.0)], &[(0.5001, 2.0)]);
        restart.is_snapshot = false; // именно дельтой, как это и приходит
        b.apply(&restart).unwrap();

        assert_eq!(b.depth(Side::Bid), 1);
        assert_eq!(b.depth(Side::Ask), 1);
        assert_eq!(b.qty_lots_at(Side::Bid, px(1.0000) / TICK), 0);
        assert_eq!(b.best_bid_tick_opt(), Some(px(0.5000) / TICK));
        assert_eq!(b.last_u(), Some(1));
    }

    /// Смена шага цены посреди записи обязана быть видна сразу. Без этой проверки
    /// дельты в тиках тихо поехали бы по масштабу в данных, которые не восстановить.
    #[test]
    fn price_off_tick_is_rejected() {
        let mut b = book();
        let bad = Update {
            is_snapshot: true,
            u: 10,
            seq: 10,
            cts_ms: 1,
            bids: vec![(px(1.00005), px(1.0))],
            asks: vec![],
        };
        let err = b.apply(&bad).unwrap_err();
        assert!(matches!(err, ApplyError::PriceNotOnTick { .. }));
    }

    #[test]
    fn qty_off_step_is_rejected() {
        let mut b = book();
        let bad = Update {
            is_snapshot: true,
            u: 10,
            seq: 10,
            cts_ms: 1,
            bids: vec![(px(1.0000), px(0.0005))],
            asks: vec![],
        };
        let err = b.apply(&bad).unwrap_err();
        assert!(matches!(err, ApplyError::QtyNotOnStep { .. }));
    }

    #[test]
    fn crossed_book_is_rejected() {
        let mut b = book();
        let bad = snapshot(10, &[(1.0002, 5.0)], &[(1.0001, 4.0)]);
        let err = b.apply(&bad).unwrap_err();
        assert!(matches!(err, ApplyError::Crossed { .. }));
    }

    #[test]
    fn levels_iterate_from_the_best_price_inward() {
        let mut b = book();
        b.apply(&snapshot(
            10,
            &[(0.9998, 1.0), (1.0000, 5.0), (0.9999, 3.0)],
            &[(1.0002, 2.0), (1.0001, 4.0)],
        ))
        .unwrap();

        let bids: Vec<i64> = b.levels(Side::Bid).map(|(t, _)| t).collect();
        assert_eq!(
            bids,
            vec![px(1.0000) / TICK, px(0.9999) / TICK, px(0.9998) / TICK]
        );

        let asks: Vec<i64> = b.levels(Side::Ask).map(|(t, _)| t).collect();
        assert_eq!(asks, vec![px(1.0001) / TICK, px(1.0002) / TICK]);
    }

    /// A7: логика уровней пишется generic над `MarketDepth` крейта, и эта книга
    /// его удовлетворяет. Тест вызывает трейт через обобщённую функцию — если бы
    /// реализация была адаптером, здесь бы понадобилась вторая.
    #[test]
    fn book_satisfies_the_crate_market_depth_trait() {
        use hftbacktest::depth::{MarketDepth, INVALID_MAX, INVALID_MIN};

        fn spread<MD: MarketDepth>(d: &MD) -> f64 {
            d.best_ask() - d.best_bid()
        }
        fn best_bid_tick<MD: MarketDepth>(d: &MD) -> i64 {
            d.best_bid_tick()
        }

        let mut b = book();
        // Пустая книга обязана отдавать сентинелы крейта, а не ноль: ноль —
        // это цена, и стратегия приняла бы его за настоящую.
        assert_eq!(best_bid_tick(&b), INVALID_MIN);
        assert_eq!(MarketDepth::best_ask_tick(&b), INVALID_MAX);

        b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
            .unwrap();

        assert!((spread(&b) - 0.0001).abs() < 1e-9);
        assert_eq!(best_bid_tick(&b), px(1.0000) / TICK);
        assert!((MarketDepth::tick_size(&b) - 0.0001).abs() < 1e-12);
        assert!((MarketDepth::lot_size(&b) - 0.001).abs() < 1e-12);
        assert!((MarketDepth::best_bid_qty(&b) - 5.0).abs() < 1e-9);
        assert!((b.bid_qty_at_tick(px(1.0000) / TICK) - 5.0).abs() < 1e-9);
        assert_eq!(b.ask_qty_at_tick(px(0.5) / TICK), 0.0);
    }

    /// Целые тики вместо `f64` существуют ровно ради этого случая: цена, которую
    /// сложение с плавающей точкой сдвинуло бы на последнем бите, обязана попасть
    /// в тот же уровень, а не породить соседний.
    #[test]
    fn prices_that_break_f64_land_on_one_level() {
        let mut b = book();
        b.apply(&snapshot(10, &[(0.3, 1.0)], &[(0.4, 1.0)]))
            .unwrap();

        let sum = 0.1_f64 + 0.2_f64; // != 0.3 в f64
        assert_ne!(sum, 0.3_f64);

        let tick_from_sum = px(sum) / TICK;
        let tick_from_literal = px(0.3) / TICK;
        assert_eq!(tick_from_sum, tick_from_literal);
        assert_eq!(b.qty_lots_at(Side::Bid, tick_from_sum), px(1.0) / STEP);
    }
}
