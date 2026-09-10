//! Жизнь уровня и шесть признаков его истории (план, §1.1).
//!
//! Уровень **рождается**, когда размер впервые превышает порог `H3`;
//! **умирает**, когда размер падает ниже 20% своего максимума или цена уходит
//! за топ-50. Повторное превышение на той же цене — **новый** уровень, объём
//! через разрыв не переносится.
//!
//! Правила, зафиксированные здесь, потому что план допускает одно прочтение
//! только вместе с ними:
//!
//! - Порог рождения строгий: размер должен быть **больше** `H3`, равенство
//!   не рождает. Порог смерти строгий в другую сторону: мёртв, когда размер
//!   **строго ниже** 20% максимума; ровно 20% — ещё жив (проверяется тестом
//!   на уровне L3: `28 * 5 == 140` не убивает, `27 * 5 < 140` убивает).
//! - Смерть считается целочисленно, `5 * size < max` в 128 битах: граница
//!   проходит по трейту с крейтом, глубже целые (`ARCHITECTURE.md` A1).
//! - Рождение только внутри топ-50: наблюдение вне топ-50 с размером выше
//!   порога уровень не создаёт — такой уровень по определению уже мёртв.
//! - Отсутствие тика в кадре читается как размер ноль: либо уровень снят,
//!   либо цена ушла из отслеживаемого окна — в обоих случаях он мёртв по
//!   правилу «ниже 20%» (максимум всегда положителен, раз рождение строгое).
//! - `repeat_count` считает **все** прошлые рождения на этой цене и стороне
//!   строго внутри скользящего часа, включая рождения прогревных уровней,
//!   которых нет в выборке: они рождались буквально, план спрашивает именно
//!   это. Граница окна строгая: рождение ровно час назад уже не считается.
//! - `repriced` — эвристика, в гейты не входит: в том же кадре, где уровень
//!   умер, на соседней цене (`tick ± 1`) той же стороны родился уровень
//!   сопоставимого размера — размер новорождённого внутри удвоенного
//!   максимума умершего в обе стороны. Печатается как есть.
//! - Монотонность — точная, а не «в основном рос»: пара последовательных
//!   наблюдений с убыванием, случившаяся не позже кадра максимума, гасит
//!   флаг. Убывание после максимума флаг не трогает.
//! - `time_to_max_ms` меряется до **первого** достижения максимума.
//! - Прогрев: рождения раньше `первый_кадр + warmup_ms` отслеживаются (нужны
//!   для `repeat_count`), но в выборку не попадают. Уровень, видимый уже в
//!   первом кадре с размером выше порога, считается рождённым в первом кадре:
//!   более ранней метки у реплея нет.
//! - Порядок выдачи детерминирован: смерти одного кадра идут по возрастанию
//!   `(сторона, тик)`. Один и тот же журнал, прогнанный дважды, даёт
//!   побайтово одинаковый вывод — проверяемое свойство A1/A2 из архитектуры.
//!
//! Классификация исхода (`eaten`/`pulled`/`mixed`, шаг 1.2) — этот модуль:
//! объём трейдов с агрессором против уровня копится в `observe_trade`,
//! на смерти пишется в `traded_lots`, правило 70/20 читает `outcome`.
//! Блочные сделки в объём не входят; сторона агрессора решает, чей уровень
//! трейд ест: бид — продавец, аск — покупатель. Склейка по времени матчинга:
//! у трейда берётся `exch_ms` (время исполнения), у кадра — метка кадра,
//! подачи вне жизни уровня не считаются. Вызывающий подаёт события
//! в неубывающем времени матчинга и уже перевёл цену в тики,
//! а количество — в лоты: здесь только целые, кучи нет.

use std::collections::{BTreeMap, VecDeque};

use crate::book::Side;

/// Конфигурация трекера. Порог `H3` и окно часа — параметры, а не хардкод:
/// их предрегистрированные значения подставляет вызывающий шагом позже.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelsConfig {
    /// Порог рождения в лотах: уровень рождается при размере строго больше.
    pub h3_lots: i64,
    /// Прогрев в миллисекундах: рождения раньше него не попадают в выборку.
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в миллисекундах.
    pub repeat_window_ms: i64,
}

/// Одно наблюдение уровня в кадре одной стороны.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelObs {
    /// Цена в тиках.
    pub tick: i64,
    /// Размер в лотах.
    pub size_lots: i64,
    /// Входит ли цена в топ-50 этой стороны в этом кадре.
    pub in_top50: bool,
}

/// Каким правилом уровень умер. Нужно шагу 1.1, чтобы различать два правила
/// смерти из плана, а не смешивать их в одну кучу.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeathKind {
    /// Размер упал строго ниже 20% максимума (включая исчезновение из кадра).
    BelowFraction,
    /// Цена ушла за топ-50 при живом размере.
    LeftTop,
}

/// Исход уровня по правилу 70/20 из плана (§1.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// Объём против уровня ≥ 70% максимума: ликвидность съели.
    Eaten,
    /// Объём против уровня ≤ 20% максимума: уровень сняли.
    Pulled,
    /// Между 20% и 70%: смешанный исход.
    Mixed,
}

/// Правило 70/20 строго целочисленно, без деления: `10 * traded >= 7 * max`
/// есть eaten, `5 * traded <= max` есть pulled, иначе mixed. Границы
/// включительные: ровно 70% — eaten, ровно 20% — pulled.
pub fn classify_outcome(traded_lots: i64, size_max: i64) -> Outcome {
    let t = traded_lots as i128;
    let m = size_max as i128;
    if t * 10 >= m * 7 {
        Outcome::Eaten
    } else if t * 5 <= m {
        Outcome::Pulled
    } else {
        Outcome::Mixed
    }
}

/// Один трейд ленты, уже переведённый вызывающим в тики и лоты.
/// Сторона — это сторона агрессора: она выбирает, какой уровень трейд ест.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeHit {
    /// Цена сделки в тиках: обязана совпасть с тиком уровня точь-в-точь.
    pub tick: i64,
    /// Размер сделки в лотах.
    pub lots: i64,
    /// `true` — агрессор-покупатель (ест аск), `false` — продавец (ест бид).
    pub aggressor_is_buy: bool,
    /// Блочная сделка: видимую ликвидность не потребляет, в объём не идёт.
    pub block: bool,
    /// Время исполнения на матчинге. Сравнимо с меткой кадра; подачи
    /// раньше рождения уровня не считаются.
    pub exch_ms: i64,
}

/// Запись умершего уровня: ключи, шесть признаков истории и место под 1.2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelRecord {
    /// Сторона книги (нужна шагу 2.1 для полярности по стороне).
    pub side: Side,
    /// Цена в тиках.
    pub price_tick: i64,
    /// Кадр рождения, мс.
    pub birth_ms: i64,
    /// Кадр смерти, мс.
    pub death_ms: i64,
    /// Признак 1: время жизни, мс.
    pub lifetime_ms: i64,
    /// Признак 2: максимум размера в лотах (читает шаг 1.2 для правила 70/20).
    pub size_max: i64,
    /// Признак 3: время до первого достижения максимума, мс.
    pub time_to_max_ms: i64,
    /// Признак 4: размер рос монотонно вплоть до максимума.
    pub size_monotonic: bool,
    /// Признак 5: сколько уровней уже рождалось на этой цене за скользящий час.
    pub repeat_count: u32,
    /// Признак 6: эвристика переставления (печатается, в гейты не входит).
    pub repriced: bool,
    /// Какое из двух правил смерти сработало.
    pub death: DeathKind,
    /// Объём трейдов против уровня в лотах за его жизнь (шаг 1.2).
    pub traded_lots: i64,
}

impl LevelRecord {
    /// Исход по правилу 70/20 от накопленного объёма против максимума.
    pub fn outcome(&self) -> Outcome {
        classify_outcome(self.traded_lots, self.size_max)
    }
}

/// Живой уровень: всё состояние — несколько целых, кучи нет.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Live {
    birth_ms: i64,
    max: i64,
    max_ms: i64,
    prev: i64,
    first_decrease_ms: Option<i64>,
    repeat: u32,
    seen_frame: u64,
    seen_top50: bool,
    seen_size: i64,
    traded: i64,
}

/// Трекер уровней. Состояние между кадрами — две карты с предвыделенными
/// ёмкостями и переиспользуемый буфер новорождённых: установившийся кадр
/// без рождений и смертей не трогает кучу вообще (требование гейта GC).
pub struct LevelTracker {
    cfg: LevelsConfig,
    live: BTreeMap<(u8, i64), Live>,
    births: BTreeMap<(u8, i64), VecDeque<i64>>,
    newborns: Vec<(u8, i64, i64)>,
    sweep: Vec<(u8, i64)>,
    start_ms: Option<i64>,
    frame: u64,
}

fn side_key(side: Side) -> u8 {
    match side {
        Side::Bid => 0,
        Side::Ask => 1,
    }
}

fn side_of(key: (u8, i64)) -> Side {
    if key.0 == 0 {
        Side::Bid
    } else {
        Side::Ask
    }
}

/// Строго ниже 20%: `5 * size < max` без деления и без остатка.
fn below_fraction(size: i64, max: i64) -> bool {
    size as i128 * 5 < max as i128
}

/// Сопоставимый размер для эвристики `repriced`: внутри удвоения в обе
/// стороны, строго целочисленно.
fn comparable_size(a: i64, b: i64) -> bool {
    let (a, b) = (a as i128, b as i128);
    a <= 2 * b && b <= 2 * a
}

impl LevelTracker {
    /// Создаёт трекер. Порог должен быть положителен, окно — тоже, прогрев
    /// неотрицателен: нулевой порог рождал бы уровень из пустого места.
    pub fn new(cfg: LevelsConfig) -> Self {
        assert!(cfg.h3_lots > 0, "порог H3 обязан быть положителен");
        assert!(cfg.warmup_ms >= 0, "прогрев не может быть отрицателен");
        assert!(
            cfg.repeat_window_ms > 0,
            "окно repeat_count обязано быть положительно"
        );
        Self {
            cfg,
            live: BTreeMap::new(),
            births: BTreeMap::new(),
            newborns: Vec::with_capacity(8),
            sweep: Vec::with_capacity(8),
            start_ms: None,
            frame: 0,
        }
    }

    /// Сколько уровней живо прямо сейчас. Нужно тестам, чтобы убедиться, что
    /// фикстура никого не потеряла и не оставила висеть.
    pub fn live_count(&self) -> usize {
        self.live.len()
    }

    /// Один кадр одной стороны. Умершие за кадр дописываются в `out`
    /// в порядке возрастания цены; ёмкость `out` — забота вызывающего, трекер
    /// её не растит сам и в горячем пути не аллоцирует.
    pub fn observe_frame(
        &mut self,
        ts_ms: i64,
        side: Side,
        levels: &[LevelObs],
        out: &mut Vec<LevelRecord>,
    ) {
        if self.start_ms.is_none() {
            self.start_ms = Some(ts_ms);
        }
        self.frame += 1;
        let frame = self.frame;
        let h3 = self.cfg.h3_lots;
        let window = self.cfg.repeat_window_ms;
        let s = side_key(side);

        self.newborns.clear();
        for ob in levels {
            let key = (s, ob.tick);
            match self.live.get_mut(&key) {
                Some(lv) => {
                    lv.seen_frame = frame;
                    lv.seen_top50 = ob.in_top50;
                    lv.seen_size = ob.size_lots;
                    if ob.size_lots < lv.prev && lv.first_decrease_ms.is_none() {
                        lv.first_decrease_ms = Some(ts_ms);
                    }
                    lv.prev = ob.size_lots;
                    if ob.size_lots > lv.max {
                        lv.max = ob.size_lots;
                        lv.max_ms = ts_ms;
                    }
                }
                None => {
                    if ob.in_top50 && ob.size_lots > h3 {
                        let repeat = self.count_prior_births(key, ts_ms, window);
                        self.live.insert(
                            key,
                            Live {
                                birth_ms: ts_ms,
                                max: ob.size_lots,
                                max_ms: ts_ms,
                                prev: ob.size_lots,
                                first_decrease_ms: None,
                                repeat,
                                seen_frame: frame,
                                seen_top50: true,
                                seen_size: ob.size_lots,
                                traded: 0,
                            },
                        );
                        self.newborns.push((s, ob.tick, ob.size_lots));
                    }
                }
            }
        }

        // Свип двухфазный и по своей стороне: кадр несёт одну сторону, и
        // отсутствие тика читается как ноль только в ней — уровни второй
        // стороны этот вызов не трогает (иначе бид и аск убивали бы друг друга
        // по очереди на каждом штампе). Итерация карты уже идёт по возрастанию
        // ключа — порядок выдачи детерминирован. Куча не растёт, пока хватает
        // ёмкостей буферов.
        let live = &self.live;
        let sweep = &mut self.sweep;
        sweep.clear();
        for (key, lv) in live.iter() {
            if key.0 != s {
                continue;
            }
            if lv.seen_frame != frame || !lv.seen_top50 || below_fraction(lv.seen_size, lv.max) {
                sweep.push(*key);
            }
        }
        let newborns = &self.newborns;
        let warm_end = self
            .start_ms
            .unwrap_or(ts_ms)
            .saturating_add(self.cfg.warmup_ms);
        let live = &mut self.live;
        for (ks, tick) in self.sweep.drain(..) {
            // Ключ только что найден в свипе, который построен обходом `live`
            // выше без единой вставки между, — отсутствие было бы дефектом
            // логики, а не данных. Паники при этом нет по режиму линтов:
            // в релизе дефект даст пропуск уровня (видимый), а в дебаге —
            // срабатывание ассёрта ниже.
            debug_assert!(live.contains_key(&(ks, tick)));
            let Some(lv) = live.remove(&(ks, tick)) else {
                continue;
            };
            if lv.birth_ms < warm_end {
                continue;
            }
            let kind = if lv.seen_frame == frame && !lv.seen_top50 {
                DeathKind::LeftTop
            } else {
                DeathKind::BelowFraction
            };
            let repriced = newborns.iter().any(|&(ns, nt, nsize)| {
                ns == ks
                    && nt.checked_sub(tick).is_some_and(|d| d == 1 || d == -1)
                    && comparable_size(nsize, lv.max)
            });
            out.push(LevelRecord {
                side: side_of((ks, tick)),
                price_tick: tick,
                birth_ms: lv.birth_ms,
                death_ms: ts_ms,
                lifetime_ms: ts_ms - lv.birth_ms,
                size_max: lv.max,
                time_to_max_ms: lv.max_ms - lv.birth_ms,
                size_monotonic: lv.first_decrease_ms.is_none_or(|t| t > lv.max_ms),
                repeat_count: lv.repeat,
                repriced,
                death: kind,
                traded_lots: lv.traded,
            });
        }
    }

    /// Один трейд ленты. Находит живой уровень той стороны, которую трейд ест
    /// на этом тике, и добавляет объём — иначе молча пропускает. Блочные,
    /// нулевые и поданные раньше рождения не считаются. Поиск в карте
    /// кучу не трогает, внутри — одно целое сложение с насыщением.
    pub fn observe_trade(&mut self, tr: TradeHit) {
        if tr.block || tr.lots <= 0 {
            return;
        }
        let key = (u8::from(tr.aggressor_is_buy), tr.tick);
        if let Some(lv) = self.live.get_mut(&key) {
            if tr.exch_ms < lv.birth_ms {
                return;
            }
            lv.traded = lv.traded.saturating_add(tr.lots);
        }
    }

    /// Сколько рождений уже было на этом ключе строго внутри окна, и запись
    /// текущего. Очередь чистится спереди: старые рождения выпадают сами.
    fn count_prior_births(&mut self, key: (u8, i64), ts_ms: i64, window_ms: i64) -> u32 {
        let q = self.births.entry(key).or_default();
        let cutoff = ts_ms - window_ms;
        while q.front().is_some_and(|&t| t <= cutoff) {
            q.pop_front();
        }
        let n = u32::try_from(q.len()).unwrap_or(u32::MAX);
        q.push_back(ts_ms);
        n
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const H3: i64 = 100;
    const HOUR_MS: i64 = 3_600_000;

    fn cfg() -> LevelsConfig {
        LevelsConfig {
            h3_lots: H3,
            warmup_ms: 0,
            repeat_window_ms: HOUR_MS,
        }
    }

    fn ob(tick: i64, size: i64) -> LevelObs {
        LevelObs {
            tick,
            size_lots: size,
            in_top50: true,
        }
    }

    fn ob_out(tick: i64, size: i64) -> LevelObs {
        LevelObs {
            tick,
            size_lots: size,
            in_top50: false,
        }
    }

    /// Свёртка аргументов в пары, чтобы помощник не спорил с лимитом числа
    /// параметров: `span` — (рождение, смерть), `peak` — (максимум, время до него).
    fn rec(
        tick: i64,
        span: (i64, i64),
        peak: (i64, i64),
        mono: bool,
        repeat: u32,
        repriced: bool,
        kind: DeathKind,
    ) -> LevelRecord {
        LevelRecord {
            side: Side::Bid,
            price_tick: tick,
            birth_ms: span.0,
            death_ms: span.1,
            lifetime_ms: span.1 - span.0,
            size_max: peak.0,
            time_to_max_ms: peak.1,
            size_monotonic: mono,
            repeat_count: repeat,
            repriced,
            death: kind,
            traded_lots: 0,
        }
    }

    /// Синтетика §1.1: четыре известных уровня плюс один вспомогательный.
    ///
    /// L1 (тик 1000) — съеден: затухание 120→150→180→30, смерть по доле.
    /// L2 (тик 2000) — снят: 110→90→200 и уход за топ-50 при живом размере;
    ///   провал 110→90 до максимума гасит монотонность.
    /// L3 (тик 3000) — частичный: 130→130→140→28 (ровно 20% — жив) →27,
    ///   смерть только на 27. Проверяет строгость границы.
    /// L4 (тик 1000) — пересоздан: новое рождение после смерти L1, поэтому
    ///   `repeat_count == 1`; умирает в кадре, где на соседнем тике 1001
    ///   рождается сопоставимый размер, поэтому `repriced == true`.
    /// L5 (тик 1001) — вспомогательный триггер `repriced` для L4: рождение
    ///   150 против максимума 170 укладывается в удвоение; `time_to_max == 0`,
    ///   потому что максимум взят уже кадром рождения.
    #[test]
    fn four_known_levels_give_known_classes_and_features() {
        let mut tr = LevelTracker::new(cfg());
        let mut out = Vec::with_capacity(16);

        tr.observe_frame(
            1000,
            Side::Bid,
            &[ob(1000, 120), ob(2000, 110), ob(3000, 130)],
            &mut out,
        );
        tr.observe_frame(
            2000,
            Side::Bid,
            &[ob(1000, 150), ob(2000, 90), ob(3000, 130)],
            &mut out,
        );
        tr.observe_frame(
            3000,
            Side::Bid,
            &[ob(1000, 180), ob(2000, 200), ob(3000, 140)],
            &mut out,
        );
        tr.observe_frame(
            4000,
            Side::Bid,
            &[ob(1000, 30), ob_out(2000, 60), ob(3000, 140)],
            &mut out,
        );
        assert_eq!(
            out,
            vec![
                rec(
                    1000,
                    (1000, 4000),
                    (180, 2000),
                    true,
                    0,
                    false,
                    DeathKind::BelowFraction
                ),
                rec(
                    2000,
                    (1000, 4000),
                    (200, 2000),
                    false,
                    0,
                    false,
                    DeathKind::LeftTop
                ),
            ],
            "кадр 4000: L1 съеден по доле, L2 снят уходом за топ-50"
        );

        tr.observe_frame(5000, Side::Bid, &[ob(1000, 160), ob(3000, 28)], &mut out);
        assert_eq!(
            out.len(),
            2,
            "кадр 5000: смертей нет, L3 держится ровно на 20%"
        );

        tr.observe_frame(6000, Side::Bid, &[ob(1000, 170), ob(3000, 27)], &mut out);
        assert_eq!(
            out[2],
            rec(
                3000,
                (1000, 6000),
                (140, 2000),
                true,
                0,
                false,
                DeathKind::BelowFraction
            ),
            "L3 умер только ниже строгой границы, спад после максимума монотонность не гасит"
        );

        tr.observe_frame(7000, Side::Bid, &[ob(1000, 10), ob(1001, 150)], &mut out);
        assert_eq!(
            out[3],
            rec(
                1000,
                (5000, 7000),
                (170, 1000),
                true,
                1,
                true,
                DeathKind::BelowFraction
            ),
            "L4 — новый уровень на той же цене: повтор учтён, переставление замечено"
        );

        tr.observe_frame(8000, Side::Bid, &[], &mut out);
        assert_eq!(
            out[4],
            rec(
                1001,
                (7000, 8000),
                (150, 0),
                true,
                0,
                false,
                DeathKind::BelowFraction
            ),
            "вспомогательный L5: максимум кадром рождения, смерть исчезновением"
        );

        assert_eq!(out.len(), 5);
        assert_eq!(
            tr.live_count(),
            0,
            "фикстура обязана никого не оставлять висеть"
        );
        assert!(
            out.iter().all(|r| r.traded_lots == 0),
            "объём против уровня заполняет шаг 1.2, здесь всегда ноль"
        );
    }

    /// Прогрев 60 минут (H3): уровень, рождённый до конца прогрева, в выборке
    /// отсутствует — но его рождение считается для `repeat_count` позднего
    /// уровня на той же цене: рождался он буквально.
    #[test]
    fn warmup_births_are_tracked_but_not_emitted() {
        let cfg = LevelsConfig {
            h3_lots: H3,
            warmup_ms: HOUR_MS,
            repeat_window_ms: HOUR_MS,
        };
        let mut tr = LevelTracker::new(cfg);
        let mut out = Vec::with_capacity(8);

        tr.observe_frame(0, Side::Bid, &[ob(5000, 50)], &mut out);
        tr.observe_frame(3_500_000, Side::Bid, &[ob(5000, 120)], &mut out);
        tr.observe_frame(3_510_000, Side::Bid, &[ob(5000, 10)], &mut out);
        assert!(
            out.is_empty(),
            "прогревный уровень обязан отсутствовать в выборке"
        );

        tr.observe_frame(3_700_000, Side::Bid, &[ob(5000, 130)], &mut out);
        tr.observe_frame(3_800_000, Side::Bid, &[ob(5000, 20)], &mut out);
        assert_eq!(
            out,
            vec![LevelRecord {
                side: Side::Bid,
                price_tick: 5000,
                birth_ms: 3_700_000,
                death_ms: 3_800_000,
                lifetime_ms: 100_000,
                size_max: 130,
                time_to_max_ms: 0,
                size_monotonic: true,
                repeat_count: 1,
                repriced: false,
                death: DeathKind::BelowFraction,
                traded_lots: 0,
            }],
            "выживает только послепрогревный уровень, повтор прогревного учтён"
        );
        assert_eq!(tr.live_count(), 0);
    }

    /// Детерминизм реплея (требование архитектуры к шагу 1.1): одна и та же
    /// последовательность кадров даёт побайтово одинаковый вывод.
    #[test]
    fn same_frames_twice_give_byte_identical_output() {
        fn replay() -> Vec<LevelRecord> {
            let mut tr = LevelTracker::new(cfg());
            let mut out = Vec::with_capacity(16);
            tr.observe_frame(1000, Side::Bid, &[ob(1000, 120), ob(2000, 110)], &mut out);
            tr.observe_frame(2000, Side::Bid, &[ob(1000, 150), ob(2000, 90)], &mut out);
            tr.observe_frame(3000, Side::Bid, &[ob(1000, 180), ob(2000, 200)], &mut out);
            tr.observe_frame(4000, Side::Bid, &[ob(1000, 30), ob_out(2000, 60)], &mut out);
            tr.observe_frame(5000, Side::Ask, &[ob(1000, 160)], &mut out);
            tr.observe_frame(6000, Side::Ask, &[ob(1000, 10)], &mut out);
            out
        }
        assert_eq!(replay(), replay());
    }

    /// Гейт GC для шага 1.1: установившийся кадр без рождений и смертей
    /// не аллоцирует. Замер идёт через счётчик на глобальном аллокаторе,
    /// цены те же, что в прогреве, — новых ключей в картах не возникает.
    #[test]
    fn steady_frames_allocate_nothing() {
        let mut tr = LevelTracker::new(cfg());
        let mut out = Vec::with_capacity(16);
        // Прогрев: рождения и смерти растягивают все буферы до рабочего размера.
        tr.observe_frame(1000, Side::Bid, &[ob(1000, 120), ob(1001, 130)], &mut out);
        tr.observe_frame(2000, Side::Bid, &[ob(1000, 150), ob(1001, 160)], &mut out);
        tr.observe_frame(3000, Side::Bid, &[ob(1000, 10), ob(1001, 10)], &mut out);
        out.clear();
        tr.observe_frame(4000, Side::Bid, &[ob(1000, 140), ob(1001, 150)], &mut out);
        tr.observe_frame(5000, Side::Bid, &[ob(1000, 145), ob(1001, 155)], &mut out);
        out.clear();

        let (_, counts) = crate::alloc_count::measure(|| {
            for i in 0..1000 {
                // Обновления живых уровней плюс промах по цене, которой нет:
                // ни вставка, ни поиск отсутствующего ключа кучу не трогают.
                tr.observe_frame(
                    6000 + i,
                    Side::Bid,
                    &[ob(1000, 145), ob(1001, 155), ob(9999, 10)],
                    &mut out,
                );
                out.clear();
            }
        });
        assert_eq!(counts.allocations, 0, "горячий путь обязан не аллоцировать");
        assert_eq!(tr.live_count(), 2);
    }

    /// Граница модулей (требование архитектуры к шагу 1.1): чистая логика не
    /// знает про транспорт и системные часы, целые не размениваются на
    /// приближённые числа. Проверка — грепом по собственному исходнику.
    ///
    /// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
    /// эту же проверку сам на себя.
    #[test]
    fn module_stays_detached_from_transport_clocks_and_approx_numbers() {
        const SRC: &str = include_str!("levels.rs");
        let banned = [
            concat!("by", "bit"),
            concat!("tok", "io"),
            concat!("Inst", "ant"),
            concat!("System", "Time"),
            concat!("f", "64"),
            concat!("std::", "time"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }

    /// Шаг 1.2: объём трейдов с агрессором против уровня делит исходы ровно
    /// по правилу 70/20; чужая сторона, блочные сделки и время вне жизни
    /// уровня в объём не попадают. Склейка — по времени матчинга `T`
    /// (поле `exch_ms`) против жизни уровня, а не по порядку подачи.
    #[test]
    fn aggressor_volume_splits_eaten_pulled_and_mixed() {
        use Outcome::{Eaten, Mixed, Pulled};
        let mut tr = LevelTracker::new(cfg());
        let mut out = Vec::with_capacity(16);

        tr.observe_frame(
            1000,
            Side::Bid,
            &[ob(1000, 200), ob(2000, 200), ob(3000, 200)],
            &mut out,
        );
        tr.observe_frame(1000, Side::Ask, &[ob(4000, 200)], &mut out);

        // Съеден (ровно 70%): 100 + 40 своих, чужое и блочное мимо.
        tr.observe_trade(TradeHit {
            tick: 1000,
            lots: 100,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 2000,
        });
        tr.observe_trade(TradeHit {
            tick: 1000,
            lots: 500,
            aggressor_is_buy: true,
            block: false,
            exch_ms: 2100,
        });
        tr.observe_trade(TradeHit {
            tick: 1000,
            lots: 500,
            aggressor_is_buy: false,
            block: true,
            exch_ms: 2200,
        });
        tr.observe_trade(TradeHit {
            tick: 1000,
            lots: 50,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 500,
        });
        tr.observe_trade(TradeHit {
            tick: 1000,
            lots: 40,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 3000,
        });
        // Снят (ровно 20%).
        tr.observe_trade(TradeHit {
            tick: 2000,
            lots: 40,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 2500,
        });
        tr.observe_trade(TradeHit {
            tick: 2000,
            lots: 100,
            aggressor_is_buy: true,
            block: false,
            exch_ms: 2600,
        });
        // Середина (50%).
        tr.observe_trade(TradeHit {
            tick: 3000,
            lots: 100,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 2500,
        });
        // Аск ест только покупатель-агрессор.
        tr.observe_trade(TradeHit {
            tick: 4000,
            lots: 150,
            aggressor_is_buy: true,
            block: false,
            exch_ms: 2500,
        });
        tr.observe_trade(TradeHit {
            tick: 4000,
            lots: 200,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 2600,
        });

        tr.observe_frame(
            4000,
            Side::Bid,
            &[ob(1000, 10), ob(2000, 10), ob(3000, 10)],
            &mut out,
        );
        tr.observe_frame(4000, Side::Ask, &[ob(4000, 10)], &mut out);

        assert_eq!(out.len(), 4);
        let by_tick = |t: i64| out.iter().find(|r| r.price_tick == t).copied().unwrap();
        let eaten = by_tick(1000);
        let pulled = by_tick(2000);
        let mixed = by_tick(3000);
        let ask_eaten = by_tick(4000);
        assert_eq!(eaten.traded_lots, 140);
        assert_eq!(pulled.traded_lots, 40);
        assert_eq!(mixed.traded_lots, 100);
        assert_eq!(ask_eaten.traded_lots, 150);
        assert_eq!(eaten.outcome(), Eaten);
        assert_eq!(pulled.outcome(), Pulled);
        assert_eq!(mixed.outcome(), Mixed);
        assert_eq!(ask_eaten.outcome(), Eaten);
        assert_eq!(classify_outcome(140, 200), Eaten);
        assert_eq!(classify_outcome(40, 200), Pulled);
        assert_eq!(classify_outcome(100, 200), Mixed);

        // Трейд по мёртвому уровню после смерти ни на что не влияет.
        tr.observe_trade(TradeHit {
            tick: 1000,
            lots: 1000,
            aggressor_is_buy: false,
            block: false,
            exch_ms: 5000,
        });
        assert_eq!(tr.live_count(), 0);
        assert_eq!(out.len(), 4);
    }

    /// Гейт GC для шага 1.2: трейды на живом уровне не аллоцируют —
    /// только поиск в карте и сложение целых.
    #[test]
    fn trades_on_a_live_level_allocate_nothing() {
        let mut tr = LevelTracker::new(cfg());
        let mut out = Vec::with_capacity(16);
        tr.observe_frame(1000, Side::Bid, &[ob(1000, 200)], &mut out);
        let (_, counts) = crate::alloc_count::measure(|| {
            for i in 0..1000 {
                tr.observe_trade(TradeHit {
                    tick: 1000,
                    lots: 1,
                    aggressor_is_buy: false,
                    block: false,
                    exch_ms: 1000 + i,
                });
            }
        });
        assert_eq!(counts.allocations, 0, "трейд обязан не аллоцировать");
        assert_eq!(tr.live_count(), 1);
    }
}
