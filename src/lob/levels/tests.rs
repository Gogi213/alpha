use super::*;

const H3: i64 = 100;
const HOUR_MS: i64 = 3_600_000;

/// `percentile` с прогревом 0 — прежнее поведение до режима `floor`:
/// используется во всех тестах жизненного цикла, которым режим не важен.
fn cfg() -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: H3 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
    }
}

/// `floor` с тем же порогом — прогрев в конфиге стоит намеренно большим:
/// тест на разницу режимов обязан доказывать, что `floor` его не читает,
/// а не просто подставлять 0 и совпасть с `percentile` по умолчанию.
fn cfg_floor() -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Floor { h3_lots: H3 },
        warmup_ms: HOUR_MS,
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

/// Реплей тех же восьми кадров, что и синтетика с четырьмя известными
/// уровнями выше, — фиксирует то, что различает `H3Mode` по плану D-H3:
/// `warmup_ms` в конфиге стоит намеренно больше всего диапазона фикстуры.
fn replay_four_known_levels(cfg: LevelsConfig) -> Vec<LevelRecord> {
    let mut tr = LevelTracker::new(cfg);
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
    tr.observe_frame(5000, Side::Bid, &[ob(1000, 160), ob(3000, 28)], &mut out);
    tr.observe_frame(6000, Side::Bid, &[ob(1000, 170), ob(3000, 27)], &mut out);
    tr.observe_frame(7000, Side::Bid, &[ob(1000, 10), ob(1001, 150)], &mut out);
    tr.observe_frame(8000, Side::Bid, &[], &mut out);
    out
}

/// Синтетика с четырьмя известными уровнями (план D-H3, таск 02) — для
/// обоих режимов `H3`, с прогревом в конфиге больше всего диапазона
/// фикстуры (8000 мс против часового `warmup_ms`): `floor` обязан
/// эмитировать все пять записей (прогрева у него нет по определению
/// режима), `percentile` с тем же конфигом — ни одной (прогрев не истёк).
/// Это и есть разница режимов, которую нельзя было увидеть на `warmup_ms
/// == 0` — там оба режима совпадают тривиально.
#[test]
fn floor_mode_ignores_warmup_percentile_mode_respects_it() {
    let floor_out = replay_four_known_levels(cfg_floor());
    assert_eq!(
        floor_out.len(),
        5,
        "floor: без прогрева фикстура эмитит все пять уровней, как при warmup_ms=0"
    );

    let percentile_cfg = LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: H3 },
        warmup_ms: HOUR_MS,
        repeat_window_ms: HOUR_MS,
    };
    let percentile_out = replay_four_known_levels(percentile_cfg);
    assert!(
        percentile_out.is_empty(),
        "percentile: часовой прогрев не истёк за 8 секунд фикстуры — эмиссии нет"
    );
}

/// Прогрев 60 минут (H3): уровень, рождённый до конца прогрева, в выборке
/// отсутствует — но его рождение считается для `repeat_count` позднего
/// уровня на той же цене: рождался он буквально.
#[test]
fn warmup_births_are_tracked_but_not_emitted() {
    let cfg = LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: H3 },
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
    const SRC: &str = include_str!("../levels.rs");
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

// ---------------------------------------------------------------------------
// Касания (таск 35, В-42).
// ---------------------------------------------------------------------------

/// Порог из тикета: `H3 = 5`, уровень — 10 лотов.
fn cfg_touch() -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
    }
}

fn touch_rec(
    tick: i64,
    touch_index: u32,
    span: (i64, i64),
    sizes: (i64, i64),
    frontrun: i64,
    ended_by_death: bool,
    stack: u32,
) -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick: tick,
        touch_index,
        start_ms: span.0,
        end_ms: span.1,
        duration_ms: span.1 - span.0,
        level_birth_ms: 1000,
        size_at_touch: sizes.0,
        size_max_before: sizes.1,
        traded_during: 0,
        frontrun_lots: frontrun,
        swept_lots: frontrun,
        round_zeros: round_zeros(tick),
        ended_by_death,
        stack_levels: stack,
    }
}

/// Критерий приёмки таска 35, фикстура тикета: бид-уровень 100 (10 лотов,
/// `H3 = 5`), лучший бид 101 (3 лота — не уровень). Кадр, где 101 исчез, —
/// уровень стал лучшим, касание началось; кадр, где 101 вернулся, — касание
/// кончилось, `touch_index = 0`, `frontrun_lots` — лоты 101 с последнего
/// кадра до касания, `traded_during` — сделка внутри касания; повтор —
/// `touch_index = 1`; смерть во время касания (в том же кадре, где 101
/// вернулся, — смерть имеет приоритет) — `ended_by_death`, `end_ms` —
/// `death_ms` записи смерти. Кадры идут раз в секунду, поэтому фронтран за
/// секунду до касания (В-45) совпадает со сметённым последним шагом
/// (`swept_lots`). `stack_levels` считает только живые уровни стороны с
/// размером не ниже `H3` в окне 25 bps от цены: у тика 100 окно — 0 тиков
/// (`stack_window_ticks`), в «завале» только сам уровень; 98 и 97 дальше.
/// Смерти уровней при этом — прежние.
#[test]
fn a_live_level_at_the_best_price_is_a_touch_with_index_frontrun_and_stack() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    tr.observe_frame_with_touches(
        1000,
        b,
        &[ob(101, 3), ob(100, 10), ob(99, 2), ob(98, 7), ob(97, 20)],
        &mut out,
        &mut touches,
    );
    assert_eq!(tr.live_count(), 3, "уровни 100, 98, 97");
    assert!(
        touches.is_empty(),
        "лучшая цена 101 — не уровень, касания нет"
    );
    // 101 исчез — 100 лучший: касание идёт, записи ещё нет.
    tr.observe_frame_with_touches(
        2000,
        b,
        &[ob(100, 12), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    assert!(touches.is_empty(), "касание идёт — записи нет до конца");
    tr.observe_trade(TradeHit {
        tick: 100,
        lots: 4,
        aggressor_is_buy: false,
        block: false,
        exch_ms: 2500,
    });
    // 101 вернулся — касание 0 кончилось уходом с лучшей цены.
    tr.observe_frame_with_touches(
        3000,
        b,
        &[ob(101, 4), ob(100, 12), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    let mut want = touch_rec(100, 0, (2000, 3000), (12, 10), 3, false, 1);
    want.traded_during = 4;
    assert_eq!(touches, vec![want]);
    assert!(out.is_empty(), "смертей нет");
    touches.clear();
    // Повтор: 101 исчез снова — касание 1; фронтран — 4 лота 101 с кадра 3000.
    tr.observe_frame_with_touches(
        4000,
        b,
        &[ob(100, 12), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    assert!(touches.is_empty());
    // 101 вернулся и в том же кадре 100 упал до 1 лота (< 20 % от 12):
    // смерть, а не уход с лучшей цены.
    tr.observe_frame_with_touches(
        5000,
        b,
        &[ob(101, 4), ob(100, 1), ob(99, 2), ob(98, 7), ob(97, 4)],
        &mut out,
        &mut touches,
    );
    assert_eq!(
        touches,
        vec![touch_rec(100, 1, (4000, 5000), (12, 12), 4, true, 1)]
    );
    assert_eq!(out.len(), 1, "смерть 100");
    assert_eq!(out[0].price_tick, 100);
    assert_eq!(out[0].death, DeathKind::BelowFraction);
    assert_eq!(touches[0].end_ms, out[0].death_ms);
    assert_eq!(
        out[0].traded_lots, 4,
        "объём смерти — за всю жизнь, как раньше"
    );
    assert_eq!(tr.live_count(), 2, "98 и 97 живы");
}

/// Число нулей в конце десятичной записи тика — критерий приёмки:
/// `100 → 2`, `1010 → 1`, `1234 → 0`; потолок «3+»; ноль и знак.
#[test]
fn round_zeros_counts_trailing_decimal_zeros_up_to_three() {
    assert_eq!(round_zeros(100), 2);
    assert_eq!(round_zeros(1010), 1);
    assert_eq!(round_zeros(1234), 0);
    assert_eq!(round_zeros(1000), 3);
    assert_eq!(round_zeros(120_000), ROUND_ZEROS_CAP);
    assert_eq!(round_zeros(0), 0);
    assert_eq!(round_zeros(-100), 2);
    assert_eq!(round_zeros(i64::MIN), 0);
}

/// В-43: рождение лучшей ценой — не касание, и пока уровень остаётся лучшим
/// с рождения, касания нет; ушёл с лучшей цены и вернулся — касание с
/// индексом 0, фронтран — лоты, стоявшие перед ним на последнем кадре до
/// возврата. Аск — та же логика, индекс 0 — низший тик; `stack_levels`
/// включает сам уровень.
#[test]
fn a_level_born_at_the_best_price_touches_only_after_leaving_and_returning() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let a = Side::Ask;
    tr.observe_frame_with_touches(1000, a, &[ob(200, 10), ob(201, 3)], &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, a, &[ob(200, 15), ob(201, 3)], &mut out, &mut touches);
    // Ушёл с лучшей цены: конца касания нет — его и не было.
    tr.observe_frame_with_touches(
        3000,
        a,
        &[ob(199, 2), ob(200, 15), ob(201, 3)],
        &mut out,
        &mut touches,
    );
    assert!(touches.is_empty(), "родился лучшим и ушёл — касаний ноль");
    // Вернулся — касание 0.
    tr.observe_frame_with_touches(4000, a, &[ob(200, 15), ob(201, 3)], &mut out, &mut touches);
    assert!(touches.is_empty());
    tr.observe_frame_with_touches(
        5000,
        a,
        &[ob(199, 4), ob(200, 15), ob(201, 3)],
        &mut out,
        &mut touches,
    );
    assert_eq!(
        touches,
        vec![TouchRecord {
            side: Side::Ask,
            price_tick: 200,
            touch_index: 0,
            start_ms: 4000,
            end_ms: 5000,
            duration_ms: 1000,
            level_birth_ms: 1000,
            size_at_touch: 15,
            size_max_before: 15,
            traded_during: 0,
            frontrun_lots: 2,
            swept_lots: 2,
            round_zeros: 2,
            ended_by_death: false,
            stack_levels: 1,
        }]
    );
    assert_eq!(touches[0].age_ms(), 3000);
    assert!(out.is_empty());
}

/// В-43, кадры одной миллисекунды — один момент: уровень, родившийся не
/// лучшим и ставший лучшим другим кадром той же метки, читается как
/// родившийся лучшим — касания нет ни в этом кадре, ни пока он остаётся
/// лучшим; после ухода и возврата — касание 0.
#[test]
fn becoming_best_within_the_birth_millisecond_is_not_a_touch() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    tr.observe_frame_with_touches(1000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(1000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    assert!(touches.is_empty());
    tr.observe_frame_with_touches(4000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(5000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    assert_eq!(touches.len(), 1);
    assert_eq!(
        (
            touches[0].touch_index,
            touches[0].start_ms,
            touches[0].frontrun_lots
        ),
        (0, 4000, 3)
    );
}

/// Касания уровней прогрева считаются (индекс растёт), но не эмитируются —
/// тот же режим, что у их смертей; уровень, родившийся после прогрева,
/// эмитирует касание с индексом 0.
#[test]
fn warmup_touches_are_tracked_but_not_emitted() {
    let mut tr = LevelTracker::new(LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 5000,
        repeat_window_ms: HOUR_MS,
    });
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    // Прогревный 100: родился не лучшим, стал лучшим, ушёл — касание было,
    // записи нет.
    tr.observe_frame_with_touches(1000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &[ob(101, 3), ob(100, 10)], &mut out, &mut touches);
    assert!(
        touches.is_empty(),
        "касание прогревного уровня не эмитируется"
    );
    // 102 после прогрева: родился лучшим (не касание), ушёл, вернулся, ушёл.
    tr.observe_frame_with_touches(
        7000,
        b,
        &[ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        8000,
        b,
        &[ob(103, 3), ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        9000,
        b,
        &[ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        10_000,
        b,
        &[ob(103, 3), ob(102, 10), ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!((touches[0].price_tick, touches[0].touch_index), (102, 0));
    assert_eq!((touches[0].start_ms, touches[0].frontrun_lots), (9000, 3));
    assert_eq!(
        touches[0].stack_levels, 1,
        "102 в окне 0 тиков (25 bps от 102 тиков) — только сам; прогревный 100 дальше"
    );
    // 102 снят (смерть без касания — оно уже кончилось), 100 снова лучший:
    // второе касание прогревного уровня — по-прежнему без записи.
    tr.observe_frame_with_touches(11_000, b, &[ob(100, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        12_000,
        b,
        &[ob(101, 3), ob(100, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(out.len(), 1, "смерть 102");
    assert_eq!(out[0].price_tick, 102);
}

/// Критерий приёмки тикета 35b (В-45): фронтран — за секунду до касания, а
/// не с последнего кадра. Бид 100 000 (10 лотов) родился под лучшим
/// 100 001 (5 лотов); за 1,5 с до касания впереди стоят те же 5 лотов, на
/// последнем кадре до касания (за 200 мс) — 1 лот (ноль впереди на кадре до
/// касания невозможен по определению: там уровень уже был бы лучшим, и
/// касание началось бы кадром раньше). Ожидание: `frontrun_lots = 5`,
/// `swept_lots = 1`. Обратный случай — за секунду до касания впереди 0
/// (уровень стоял лучшим с рождения — не касание), на последнем кадре 4
/// лота: `frontrun_lots = 0`, `swept_lots = 4` — корзина «фронтран 0»
/// достижима.
#[test]
fn frontrun_is_sampled_a_second_before_the_touch_and_swept_is_the_last_frame() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let tick = 100_000;
    // Рождение под лучшим 100 001 (5 лотов): секунда 0.
    tr.observe_frame_with_touches(
        0,
        b,
        &[ob(tick + 1, 5), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    // 500 мс: впереди по-прежнему 5. 1000 мс: слот перевернулся, впереди 5.
    tr.observe_frame_with_touches(
        500,
        b,
        &[ob(tick + 1, 5), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        1000,
        b,
        &[ob(tick + 1, 5), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    // 2300 мс: впереди 1 лот (последний кадр до касания). 2500 мс: касание.
    tr.observe_frame_with_touches(
        2300,
        b,
        &[ob(tick + 1, 1), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(2500, b, &[ob(tick, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        3000,
        b,
        &[ob(tick + 1, 2), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(
        (touches[0].frontrun_lots, touches[0].swept_lots),
        (5, 1),
        "за секунду до касания (кадр 1000 мс) впереди 5, на последнем кадре (2300 мс) — 1"
    );
    touches.clear();

    // Обратный случай: аск 200 000 родился лучшим (впереди 0), 1500 мс стоял
    // лучшим, затем перед ним встали 4 лота на 199 999 и через 200 мс ушли —
    // касание с фронтраном 0 и сметённым 4.
    let a = Side::Ask;
    let at = 200_000;
    tr.observe_frame_with_touches(10_000, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(11_000, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(11_500, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        11_800,
        a,
        &[ob(at - 1, 4), ob(at, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(12_000, a, &[ob(at, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        13_000,
        a,
        &[ob(at - 1, 1), ob(at, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(touches[0].side, Side::Ask);
    assert_eq!(
        (touches[0].frontrun_lots, touches[0].swept_lots),
        (0, 4),
        "за секунду до касания впереди ничего не было, последний шаг смёл 4"
    );
    assert!(out.is_empty());
}

/// В-45: два слота фронтрана — значение с кадра не позже секунды до касания
/// даже когда слот перевернулся в самом кадре касания: наблюдения на 0, 999
/// и касание на 1000 мс — читается кадр 0 (первое наблюдение старого слота),
/// а не 999 (его последнее, оно позже секунды до касания). Уровень моложе
/// секунды — его первое наблюдение (кадр рождения).
#[test]
fn frontrun_never_reads_a_frame_later_than_a_second_before_the_touch() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let tick = 100_000;
    tr.observe_frame_with_touches(
        0,
        b,
        &[ob(tick + 1, 7), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        999,
        b,
        &[ob(tick + 1, 2), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(1000, b, &[ob(tick, 10)], &mut out, &mut touches);
    tr.observe_frame_with_touches(
        1100,
        b,
        &[ob(tick + 1, 1), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!((touches[0].frontrun_lots, touches[0].swept_lots), (7, 2));
    touches.clear();

    // Моложе секунды: рождение на 5000 под 3 лотами, касание на 5400.
    let young = 300_000;
    tr.observe_frame_with_touches(
        5000,
        b,
        &[
            ob(young + 1, 3),
            ob(young, 10),
            ob(tick + 1, 1),
            ob(tick, 10),
        ],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        5200,
        b,
        &[
            ob(young + 1, 6),
            ob(young, 10),
            ob(tick + 1, 1),
            ob(tick, 10),
        ],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        5400,
        b,
        &[ob(young, 10), ob(tick + 1, 1), ob(tick, 10)],
        &mut out,
        &mut touches,
    );
    tr.observe_frame_with_touches(
        5600,
        b,
        &[
            ob(young + 1, 1),
            ob(young, 10),
            ob(tick + 1, 1),
            ob(tick, 10),
        ],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1);
    assert_eq!(touches[0].price_tick, young);
    assert_eq!(
        (touches[0].frontrun_lots, touches[0].swept_lots),
        (3, 6),
        "старого слота нет — первое наблюдение (кадр рождения)"
    );
}

/// Критерий приёмки тикета 35b (В-45): «завал» — уровни ≥ `H3` той же
/// стороны не дальше 25 bps от цены уровня. У бида 100 000 окно — 250 тиков
/// (`stack_window_ticks`): 99 800 (200 тиков) считается, 99 700 (300 тиков)
/// — нет, сам уровень входит; уровень ниже `H3` в окне не считается.
#[test]
fn stack_counts_levels_within_25_bps_only() {
    assert_eq!(stack_window_ticks(100_000), 250);
    assert_eq!(
        stack_window_ticks(100),
        0,
        "25 bps от 100 тиков — четверть тика"
    );
    assert_eq!(stack_window_ticks(399), 0);
    assert_eq!(stack_window_ticks(400), 1);
    assert_eq!(stack_window_ticks(0), 0);
    assert_eq!(
        stack_window_ticks(-100_000),
        250,
        "модуль: арифметика тотальна"
    );

    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let tick = 100_000;
    let frame_before = [
        ob(tick + 1, 3),
        ob(tick, 10),
        ob(tick - 200, 10),
        ob(tick - 250, 20),
        ob(tick - 251, 20),
        ob(tick - 300, 30),
    ];
    let frame_touch = [
        ob(tick, 10),
        ob(tick - 200, 10),
        ob(tick - 250, 20),
        ob(tick - 251, 20),
        ob(tick - 300, 30),
    ];
    tr.observe_frame_with_touches(1000, b, &frame_before, &mut out, &mut touches);
    assert_eq!(tr.live_count(), 5);
    tr.observe_frame_with_touches(2000, b, &frame_touch, &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &frame_before, &mut out, &mut touches);
    assert_eq!(touches.len(), 1);
    assert_eq!(
        touches[0].stack_levels, 3,
        "сам 100 000, 99 800 и 99 750 (ровно 250 тиков — включительно); 99 749 и 99 700 дальше"
    );
    assert!(out.is_empty());
}

/// Гейт GC таска 35: кадры с касаниями — начало и конец на каждом кадре,
/// сделки внутри — после прогрева не аллоцируют; то же для `observe_frame`
/// без выхода касаний (буфер-заглушка переиспользуется).
#[test]
fn touching_frames_allocate_nothing() {
    let mut tr = LevelTracker::new(cfg_touch());
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let b = Side::Bid;
    let with_best = [ob(101, 3), ob(100, 10), ob(98, 7)];
    let level_best = [ob(100, 10), ob(98, 7)];
    // Прогрев: рождения, касание, конец касания растягивают буферы.
    tr.observe_frame_with_touches(1000, b, &with_best, &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, b, &level_best, &mut out, &mut touches);
    tr.observe_frame_with_touches(3000, b, &with_best, &mut out, &mut touches);
    tr.observe_frame(4000, b, &level_best, &mut out);
    tr.observe_frame(5000, b, &with_best, &mut out);
    assert_eq!(touches.len(), 1);
    touches.clear();

    let (_, counts) = crate::alloc_count::measure(|| {
        for i in 0..1000i64 {
            let ts = 6000 + 2 * i;
            tr.observe_frame_with_touches(ts, b, &level_best, &mut out, &mut touches);
            tr.observe_trade(TradeHit {
                tick: 100,
                lots: 1,
                aggressor_is_buy: false,
                block: false,
                exch_ms: ts,
            });
            tr.observe_frame_with_touches(ts + 1, b, &with_best, &mut out, &mut touches);
            touches.clear();
            tr.observe_frame(ts + 1, b, &level_best, &mut out);
            tr.observe_frame(ts + 1, b, &with_best, &mut out);
        }
    });
    assert_eq!(counts.allocations, 0, "касания обязаны не аллоцировать");
    assert_eq!(tr.live_count(), 2);
    assert!(out.is_empty(), "смертей не было");
}
