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
        approach_bps: None,
        approach_min_age_ms: 0,
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
        approach_bps: None,
        approach_min_age_ms: 0,
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
        rpi_lots: 0,
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
        approach_bps: None,
        approach_min_age_ms: 0,
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
        approach_bps: None,
        approach_min_age_ms: 0,
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
            rpi_lots: 0,
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
/// приближённые числа, а живые карты (`live`/`births`/`carry`) не возвращаются
/// к дереву-словарю, которое аллоцирует узел на каждое рождение (W4б — было
/// исправлено на отсортированный `Vec` с двоичным поиском, `SortedVec` по
/// образцу `Book::HalfBook`, A4). Проверка — грепом по собственному исходнику.
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
        concat!("BTree", "Map"),
        concat!("Hash", "Map"),
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
        rpi: false,
        exch_ms: 2000,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 500,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
        exch_ms: 2100,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 500,
        aggressor_is_buy: false,
        block: true,
        rpi: false,
        exch_ms: 2200,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 50,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 500,
    });
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 40,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 3000,
    });
    // Снят (ровно 20%).
    tr.observe_trade(TradeHit {
        tick: 2000,
        lots: 40,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    tr.observe_trade(TradeHit {
        tick: 2000,
        lots: 100,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
        exch_ms: 2600,
    });
    // Середина (50%).
    tr.observe_trade(TradeHit {
        tick: 3000,
        lots: 100,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    // Аск ест только покупатель-агрессор.
    tr.observe_trade(TradeHit {
        tick: 4000,
        lots: 150,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
        exch_ms: 2500,
    });
    tr.observe_trade(TradeHit {
        tick: 4000,
        lots: 200,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
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
        rpi: false,
        exch_ms: 5000,
    });
    assert_eq!(tr.live_count(), 0);
    assert_eq!(out.len(), 4);
}

/// RPI-сделка исполнилась об **невидимую** заявку маркет-мейкера, а не об этот
/// уровень (В-55): в объём против видимой плотности она не идёт — иначе уровень
/// помечался бы «съели» за счёт чужого объёма. Считается она отдельно, чтобы
/// было видно, сколько на этой цене прошло мимо книги.
#[test]
fn rpi_trade_does_not_count_as_visible_consumption_but_is_counted_separately() {
    let mut tr = LevelTracker::new(cfg());
    let mut out = Vec::with_capacity(4);

    tr.observe_frame(1000, Side::Bid, &[ob(1000, 200)], &mut out);
    tr.observe_frame(1000, Side::Ask, &[ob(4000, 200)], &mut out);

    // 150 лотов об RPI: этого хватило бы на «съели» (это 75 % от 200), но
    // видимый уровень такие сделки не потребляют.
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 150,
        aggressor_is_buy: false,
        block: false,
        rpi: true,
        exch_ms: 2000,
    });
    // Обычная сделка на 20 лотов: ровно 20 % от 200 — «снят».
    tr.observe_trade(TradeHit {
        tick: 1000,
        lots: 20,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms: 2100,
    });

    tr.observe_frame(3000, Side::Bid, &[ob(1000, 10)], &mut out);
    tr.observe_frame(3000, Side::Ask, &[ob(4000, 10)], &mut out);

    let bid = out.iter().find(|r| r.price_tick == 1000).copied().unwrap();
    assert_eq!(
        bid.traded_lots, 20,
        "RPI-объём не входит в объём против видимого уровня"
    );
    assert_eq!(bid.rpi_lots, 150, "RPI-объём обязан считаться отдельно");
    assert_eq!(
        bid.outcome(),
        Outcome::Pulled,
        "без RPI-объёма исход — «снят»; с ним ошибочно вышло бы «съели»"
    );
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
                rpi: false,
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
        approach_bps: None,
        approach_min_age_ms: 0,
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
        frontrun_tick: None,
        swept_lots: frontrun,
        round_zeros: round_zeros(tick),
        ended_by_death,
        stack_levels: stack,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        strength_held_e2: [-1, -1, -1, -1],
        repeat_count: 0,
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
        rpi: false,
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
    // E4: сделка на 4 лота с меткой 2500 — через 0.5 с после старта касания,
    // то есть внутри всех трёх окон реакции.
    want.traded_first_s = [4, 4, 4];
    // B2: цена первого фронтранера — ближайший лучший уровень (101), на том же
    // наблюдении, что лоты фронтрана.
    want.frontrun_tick = Some(101);
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
    let mut want = touch_rec(100, 1, (4000, 5000), (12, 12), 4, true, 1);
    // Фронтран снова 101 — и цена его та же (B2).
    want.frontrun_tick = Some(101);
    // Сила «×поток»: к старту второго касания (4000) за час прошла одна
    // сделка на 4 лота (метка 2500) — оборот 4.
    want.flow_1h_lots = 4;
    assert_eq!(touches, vec![want]);
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
            frontrun_tick: Some(199),
            swept_lots: 2,
            round_zeros: 2,
            ended_by_death: false,
            stack_levels: 1,
            stack_next_tick: None,
            traded_first_s: [0; 3],
            flow_1h_lots: 0,
            // Окно 50 bps от тика 200 — один тик: сосед 199 (фронтран, 3 лота) даёт 15/3 = 500 %.
            strength_e2: [-1, -1, 50_000],
            strength_held_e2: [-1, -1, -1, -1],
            repeat_count: 0,
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
        approach_bps: None,
        approach_min_age_ms: 0,
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
                rpi: false,
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

// ---------------------------------------------------------------------------
// Режимы порога В-61 (владелец 2026-09-18): «от N» в деньгах, сила «×соседи», оба.
// ---------------------------------------------------------------------------

fn live_count(tr: &LevelTracker) -> usize {
    let mut open = Vec::new();
    tr.live_levels(&mut open);
    open.len()
}

/// Денежный пол: тик $1, лот 1 — уровень на цене 10 с размером 9 ($90) не
/// рождается при N = $100, с размером 10 ($100) — рождается (не ниже, а не
/// строго выше). На цене 1000 хватает и одного лота.
#[test]
fn notional_mode_births_by_price_times_size() {
    let mode = H3Mode::Notional {
        min_usd_e9: 100 * 1_000_000_000,
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
    };
    let cfg = LevelsConfig {
        mode,
        warmup_ms: HOUR_MS,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut out = Vec::new();
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10, 9)], &mut out);
    assert_eq!(live_count(&tr), 0, "$90 < $100");
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10, 10)], &mut out);
    assert_eq!(live_count(&tr), 1, "$100 ≥ $100");
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(1000, 1)], &mut out);
    assert_eq!(live_count(&tr), 1, "$1000 одним лотом");
    assert_eq!(out.len(), 0, "без прогрева, но и без смертей");
}

/// Сила «×соседи»: 300 % от среднего соседа в ±20 bps. У бидов кадр идёт
/// сверху вниз, у асков — снизу вверх; окно от цены 10000 и 20 bps — 20 тиков.
#[test]
fn strength_mode_compares_a_level_with_its_neighbours() {
    let mode = H3Mode::Strength {
        pct_e2: 300 * 100,
        window_bps_e2: 20 * 100,
    };
    let cfg = LevelsConfig {
        mode,
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut out = Vec::new();
    // Бид 10000×100 против соседей 9990×10 и 9985×10: 100/10 = 1000 % — рождается;
    // сами соседи (10 против среднего (100+10)/2 = 55 — 18 %) — нет.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Bid,
        &[ob(10000, 100), ob(9990, 10), ob(9985, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 1);
    // Сосед за окном (9970 — 30 тиков) не считается: уровень один — силы нет.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10000, 100), ob(9970, 1)], &mut out);
    assert_eq!(live_count(&tr), 0, "без соседей в окне сила не определена");
    // Аски (тики растут): та же картина зеркально.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Ask,
        &[ob(10000, 100), ob(10010, 10), ob(10015, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 1);
    // Ровно 300 % проходит (не строго): 30 против соседей 10 и 10.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Ask,
        &[ob(10000, 30), ob(10010, 10), ob(10015, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 1, "граница включительная");
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(
        1000,
        Side::Ask,
        &[ob(10000, 29), ob(10010, 10), ob(10015, 10)],
        &mut out,
    );
    assert_eq!(live_count(&tr), 0, "290 % — мало");
}

/// «Оба» — И: денежный пол пройден, сила нет — уровня нет; и наоборот.
#[test]
fn both_mode_requires_notional_and_strength_together() {
    let both = H3Mode::Both {
        min_usd_e9: 100 * 1_000_000_000,
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
        pct_e2: 300 * 100,
        window_bps_e2: 20 * 100,
    };
    let cfg = LevelsConfig {
        mode: both,
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut out = Vec::new();
    // Цена 10000, размер 100: $1 000 000 ≥ $100 и 1000 % силы — рождается.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10000, 100), ob(9990, 10)], &mut out);
    assert_eq!(live_count(&tr), 1);
    // Размер 100 против соседа 100: сила 100 % — нет, хотя номинал есть.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(10000, 100), ob(9990, 100)], &mut out);
    assert_eq!(live_count(&tr), 0);
    // Цена 1, размер 50 против соседа 10: сила есть, номинала $50 нет.
    let mut tr = LevelTracker::new(cfg);
    tr.observe_frame(1000, Side::Bid, &[ob(1, 50), ob(1, 10)], &mut out);
    assert_eq!(live_count(&tr), 0);
}

/// Сила «×соседи» как ось: кадр бидов 10000×100, 9990×10, 9985×10 — в ±20 bps
/// (20 тиков) у первого два соседа по 10 ⇒ 1000 %; у второго соседи 100 и 10 ⇒
/// 18.18 %; в ±10 bps (10 тиков) у третьего (9985) сосед только 9990 ⇒ 100 %;
/// одинокий уровень — `-1`.
#[test]
fn neighbour_strength_axis_is_percent_of_mean_neighbour() {
    let levels = [ob(10000, 100), ob(9990, 10), ob(9985, 10)];
    let mut prefix = vec![0i64];
    for o in &levels {
        let last = *prefix.last().unwrap();
        prefix.push(last + o.size_lots);
    }
    assert_eq!(
        neighbour_strength_e2(&levels, &prefix, 0, 20 * 100),
        100_000
    );
    assert_eq!(neighbour_strength_e2(&levels, &prefix, 1, 20 * 100), 1_818);
    assert_eq!(neighbour_strength_e2(&levels, &prefix, 2, 10 * 100), 10_000);
    let lone = [ob(10000, 100), ob(9000, 5)];
    let prefix = vec![0i64, 100, 105];
    assert_eq!(neighbour_strength_e2(&lone, &prefix, 0, 20 * 100), -1);
}

/// История силы: бид 10000 с соседом 9990 — сила 100/10 = 1000 %; на втором
/// кадре сосед вырос до 100 — сила 100/((5+100)/2) = 190.47 % (в ±20 bps от
/// 10000 сосед и 10010 с 5 лотами), дальше снова 10. Касание через 5 с после
/// рождения (уровень стал лучшим после ухода): «сейчас» 1000 %, минимум за 1 с
/// — 1000 %, за 5 с — 190.47 % (провал внутри окна), за 15 и 60 с — уровень
/// моложе окна: `-1`. `repeat_count` — 0, рождений на этой цене до этого не было.
#[test]
fn strength_history_keeps_the_minimum_over_the_window() {
    let cfg = LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 1 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(cfg);
    let mut out = Vec::new();
    let mut touches = Vec::new();
    let quiet = [ob(10010, 5), ob(10000, 100), ob(9990, 10)];
    let dip = [ob(10010, 5), ob(10000, 100), ob(9990, 100)];
    tr.observe_frame_with_touches(1000, Side::Bid, &quiet, &mut out, &mut touches);
    tr.observe_frame_with_touches(2000, Side::Bid, &dip, &mut out, &mut touches);
    for ts in [3000, 4000, 5000] {
        tr.observe_frame_with_touches(ts, Side::Bid, &quiet, &mut out, &mut touches);
    }
    // 6000: 10010 ушёл — уровень 10000 стал лучшим: касание в возрасте 5 с.
    tr.observe_frame_with_touches(
        6000,
        Side::Bid,
        &[ob(10000, 100), ob(9990, 10)],
        &mut out,
        &mut touches,
    );
    // 7000: цена ушла выше — касание кончилось.
    tr.observe_frame_with_touches(
        7000,
        Side::Bid,
        &[ob(10020, 5), ob(10000, 100), ob(9990, 10)],
        &mut out,
        &mut touches,
    );
    assert_eq!(touches.len(), 1, "{touches:?}");
    let t = &touches[0];
    assert_eq!(t.price_tick, 10000);
    assert_eq!(t.strength_e2[1], 100_000, "сейчас 1000 % в ±20 bps");
    assert_eq!(t.strength_held_e2[0], 100_000, "за 1 с провала нет");
    assert_eq!(t.strength_held_e2[1], 19_047, "за 5 с минимум — 190.47 %");
    assert_eq!(t.strength_held_e2[2], -1, "уровень моложе 15 с");
    assert_eq!(t.strength_held_e2[3], -1, "уровень моложе 60 с");
    assert_eq!(t.repeat_count, 0);
}

/// Порог в момент касания (В-66, база E1): номинал — по размеру при касании,
/// сила — по `strength_e2` окна порога; окно не из оси — `None`.
#[test]
fn threshold_is_rechecked_at_touch_time() {
    let mut t = touch_rec(100, 0, (2000, 3000), (12, 10), 3, false, 1);
    t.strength_e2 = [-1, 15_000, 20_000]; // ±20 bps — 150 %
    let both = H3Mode::Both {
        min_usd_e9: 5_000_000_000, // $5: цена 100 тиков × 0.01 = $1, шаг лота 1 → порог 5 лотов
        tick_e9: 10_000_000,
        step_e9: 1_000_000_000,
        pct_e2: 10_000,
        window_bps_e2: 2_000,
    };
    assert_eq!(both.holds_at_touch(&t), Some(true));
    t.strength_e2 = [-1, 9_900, 20_000];
    assert_eq!(
        both.holds_at_touch(&t),
        Some(false),
        "сила ниже порога при касании"
    );
    t.strength_e2 = [-1, 15_000, 20_000];
    t.size_at_touch = 1; // $1 < $5
    assert_eq!(
        both.holds_at_touch(&t),
        Some(false),
        "номинал ниже пола при касании"
    );
    let odd_window = H3Mode::Strength {
        pct_e2: 10_000,
        window_bps_e2: 1_500,
    };
    assert_eq!(odd_window.holds_at_touch(&t), None, "окно 15 bps не из оси");
    let floor = H3Mode::Floor { h3_lots: 5 };
    t.size_at_touch = 12;
    assert_eq!(floor.holds_at_touch(&t), Some(true));
}

// ---------------------------------------------------------------------------
// Подход (F1 этапа F, В-73): взвод «на подходе» и снятие.
// ---------------------------------------------------------------------------

/// Конфиг сигнала подхода: порог `H3 = 5` (как у касаний), полоса `d` bps,
/// пол возраста взвода `min_age_ms`.
fn cfg_approach(d: i64, min_age_ms: i64) -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: Some(d),
        approach_min_age_ms: min_age_ms,
    }
}

/// Кадр одной стороны с выходом подходов — как `feed_frames`: бид, затем аск.
fn frame(
    tr: &mut LevelTracker,
    ts_ms: i64,
    side: Side,
    levels: &[LevelObs],
    out: &mut Vec<LevelRecord>,
    touches: &mut Vec<TouchRecord>,
    ap: &mut Vec<ApproachRecord>,
) {
    tr.observe_frame_with_approaches(ts_ms, side, levels, out, touches, ap);
}

/// Фикстура подхода: бид-стена 10 000 (10 лотов) за лучшим бидом 10 020
/// (3 лота — не уровень), аск то 10 060 (60 bps от стены), то 10 015 (15 bps).
/// Сторона кадра смотрит на **чужую** лучшую цену: бид-кадр видит аск
/// прошлого кадра.
const WALL: [LevelObs; 2] = [
    LevelObs {
        tick: 10020,
        size_lots: 3,
        in_top50: true,
    },
    LevelObs {
        tick: 10000,
        size_lots: 10,
        in_top50: true,
    },
];

/// Взвод при входе в полосу и снятие касанием (F1, критерий приёмки):
/// цена аска 60 bps — вне полосы 20; 15 bps — взвод (возраст 2 с ≥ 500 мс);
/// стена стала лучшей ценой — подход снят касанием с `touch_start_ms`.
#[test]
fn approach_arms_within_the_band_and_disarms_on_touch() {
    let mut tr = LevelTracker::new(cfg_approach(20, 500));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "60 bps — вне полосы");
    // Аск подошёл на 15 bps; на следующем бид-кадре — взвод (записи ещё нет).
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "взвод — не запись; запись на снятии");
    // 10 020 исчез — стена стала лучшей ценой: касание и снятие подхода.
    frame(
        &mut tr,
        4000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(
        ap,
        vec![ApproachRecord {
            side: Side::Bid,
            price_tick: 10000,
            approach_index: 0,
            arm_ms: 3000,
            arm_dist_bps: 15,
            level_birth_ms: 1000,
            size_at_arm: 10,
            best_own_tick: 10020,
            best_opp_tick: 10015,
            flow_1h_lots: 0,
            // Кадр взвода: сосед 10 020 (3 лота) входит в ±20 и ±50 bps,
            // в ±10 bps (10 тиков) — нет.
            strength_e2: [-1, 33_333, 33_333],
            touch_start_ms: Some(4000),
            disarm_ms: 4000,
            disarm_reason: ApproachEnd::Touch,
        }]
    );
    assert_eq!(ap[0].age_ms(), 2000);
    assert_eq!(ap[0].duration_ms(), 1000);
    assert!(touches.is_empty(), "касание идёт — записи ещё нет");
    assert!(out.is_empty());
}

/// Снятие уходом цены за `2 × D` и повторный взвод: 60 bps > 40 — снятие
/// (без касания), 15 bps после ухода — новый взвод с индексом 1.
#[test]
fn approach_disarms_when_price_leaves_and_arms_again() {
    let mut tr = LevelTracker::new(cfg_approach(20, 0));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    // Аск вернулся на 60 bps: бид-кадр 5000 видит уход — снятие по цене.
    frame(
        &mut tr,
        4000,
        Side::Ask,
        &[ob(10060, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        5000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::PriceLeft);
    assert_eq!(ap[0].disarm_ms, 5000);
    assert_eq!(ap[0].touch_start_ms, None);
    assert_eq!(ap[0].approach_index, 0);
    assert_eq!(ap[0].duration_ms(), 2000);
    // Снова подошёл — взвод с индексом 1; стена стала лучшей — снятие касанием.
    frame(
        &mut tr,
        6000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        7000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        8000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 2, "{ap:?}");
    assert_eq!(ap[1].approach_index, 1);
    assert_eq!(ap[1].arm_ms, 7000);
    assert_eq!(ap[1].disarm_reason, ApproachEnd::Touch);
    assert_eq!(ap[1].disarm_ms, 8000);
}

/// Смерть уровня снимает взведённый подход (`LevelDeath`), и только тогда:
/// пока стена жива и цена в полосе, записи нет.
#[test]
fn approach_disarms_on_level_death() {
    let mut tr = LevelTracker::new(cfg_approach(20, 0));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty());
    // Размер стены упал до 1 лота (< 20 % от 10) — смерть: подход снят ею.
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &[ob(10020, 3), ob(10000, 1)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(out.len(), 1, "смерть уровня записана");
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::LevelDeath);
    assert_eq!(ap[0].disarm_ms, 3000);
    assert_eq!(ap[0].arm_ms, 2000);
    assert_eq!(ap[0].touch_start_ms, None);
}

/// Пол возраста (В-71) держит взвод: при `min_age = 5 с` подход не взводится
/// в 3 с (возраст 2 с) и взводится в 6 с (возраст 5 с).
#[test]
fn approach_waits_for_the_age_floor() {
    let mut tr = LevelTracker::new(cfg_approach(20, 5_000));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    for ts in [2000, 3000, 4000, 5000] {
        frame(
            &mut tr,
            ts,
            Side::Bid,
            &WALL,
            &mut out,
            &mut touches,
            &mut ap,
        );
    }
    assert!(ap.is_empty(), "возраст меньше пола — взвода нет");
    frame(
        &mut tr,
        6000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        7000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].arm_ms, 6000, "взвод на кадре возраста 5 с");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::Touch);
}

/// Пока стена — лучшая цена своей стороны, подход не взводится (В-73: «в
/// упор» вход не ставим): цена уже дошла, это касание, а не подход.
#[test]
fn approach_does_not_arm_while_the_wall_is_best() {
    let mut tr = LevelTracker::new(cfg_approach(20, 0));
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    // Стена сразу лучший бид (родилась лучшей — не касание по В-43).
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &[ob(10000, 10), ob(9990, 2)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    for ts in [2000, 3000, 4000] {
        frame(
            &mut tr,
            ts,
            Side::Bid,
            &[ob(10000, 10), ob(9990, 2)],
            &mut out,
            &mut touches,
            &mut ap,
        );
    }
    assert!(ap.is_empty(), "стена лучшая — подхода нет");
    // Ушла с лучшей цены к 10 020 — взвод возможен уже здесь.
    frame(
        &mut tr,
        5000,
        Side::Bid,
        &WALL,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        6000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].arm_ms, 5000);
}

/// Гейт F1: без `approach_bps` записи подходов прежние, а записи уровней и
/// касания при включённом сигнале — те же байты (состояние подхода их не
/// трогает).
#[test]
fn approaches_do_not_change_levels_and_touches() {
    let script: [(i64, Side, &[LevelObs]); 8] = [
        (1000, Side::Bid, &WALL),
        (1000, Side::Ask, &[ob(10060, 4)]),
        (2000, Side::Ask, &[ob(10015, 4)]),
        (3000, Side::Bid, &WALL),
        (4000, Side::Bid, &[ob(10000, 10)]),
        (5000, Side::Bid, &WALL),
        (6000, Side::Ask, &[ob(10060, 4)]),
        (7000, Side::Bid, &[ob(10020, 3), ob(10000, 1)]),
    ];
    let run = |cfg: LevelsConfig| {
        let mut tr = LevelTracker::new(cfg);
        let mut out = Vec::new();
        let mut touches = Vec::new();
        let mut ap = Vec::new();
        for &(ts, side, levels) in script.iter() {
            frame(&mut tr, ts, side, levels, &mut out, &mut touches, &mut ap);
        }
        (out, touches, ap)
    };
    let (out_off, touches_off, ap_off) = run(cfg_touch());
    let (out_on, touches_on, ap_on) = run(cfg_approach(20, 0));
    assert_eq!(out_off, out_on, "записи уровней не меняются");
    assert_eq!(touches_off, touches_on, "касания не меняются");
    assert!(ap_off.is_empty(), "без флага записей подхода нет");
    assert!(!ap_on.is_empty(), "с флагом — есть");
}

/// Порог В-66 у подхода — тот же, что у касания: слабый «×соседи» уровень не
/// взводится, сильный взводится; окно порога не из оси — взвода нет.
#[test]
fn approach_holds_the_same_strength_gate_as_touch() {
    // Стена 10 лотов, сосед 1 лот в ±20 bps: сила 1000 %. Порог 200 % — ок.
    let weak_neighbour = [ob(10020, 1), ob(10000, 10)];
    let strong_gate = LevelsConfig {
        mode: H3Mode::Strength {
            pct_e2: 20_000,
            window_bps_e2: 2_000,
        },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: Some(20),
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(strong_gate);
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(ap[0].disarm_reason, ApproachEnd::Touch);
    // Окно порога 15 bps не из оси `STRENGTH_WINDOWS_BPS` — взвода нет.
    let odd_gate = LevelsConfig {
        mode: H3Mode::Strength {
            pct_e2: 20_000,
            window_bps_e2: 1_500,
        },
        warmup_ms: 0,
        repeat_window_ms: HOUR_MS,
        approach_bps: Some(20),
        approach_min_age_ms: 0,
    };
    let mut tr = LevelTracker::new(odd_gate);
    let mut ap = Vec::with_capacity(16);
    frame(
        &mut tr,
        1000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        1000,
        Side::Ask,
        &[ob(10015, 4)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        2000,
        Side::Bid,
        &weak_neighbour,
        &mut out,
        &mut touches,
        &mut ap,
    );
    frame(
        &mut tr,
        3000,
        Side::Bid,
        &[ob(10000, 10)],
        &mut out,
        &mut touches,
        &mut ap,
    );
    assert!(ap.is_empty(), "порог не проверить — взвода нет");
}

/// Перенос возраста через полночь (аудит дизайна 22.09 Т3): уровень, стоящий на той же цене в
/// первом кадре стороны новых суток, наследует рождение прошлых суток — касание несёт возраст
/// с настоящего рождения. Уровень, появившийся позже первого кадра, — новорождённый. Без
/// переноса (`new`) и в режиме с прогревом рождение — начало суток.
#[test]
fn carried_births_survive_midnight_only_for_levels_in_the_first_frame() {
    let b = Side::Bid;
    let mut out = Vec::new();
    let mut touches = Vec::new();
    let mut day1 = LevelTracker::new(cfg_touch());
    day1.observe_frame_with_touches(
        1000,
        b,
        &[ob(101, 3), ob(100, 10), ob(98, 7), ob(97, 20)],
        &mut out,
        &mut touches,
    );
    let carry = day1.live_births();
    assert_eq!(carry.len(), 3, "100, 98, 97");

    let day2_frames = |tr: &mut LevelTracker, touches: &mut Vec<TouchRecord>| {
        let mut out = Vec::new();
        const D2: i64 = 90_000_000;
        // Первый кадр суток: 100 и 97 стоят, 98 нет.
        tr.observe_frame_with_touches(
            D2,
            b,
            &[ob(101, 3), ob(100, 10), ob(97, 20)],
            &mut out,
            touches,
        );
        // 98 вернулся позже первого кадра; 101 исчез — касание 100.
        tr.observe_frame_with_touches(
            D2 + 1000,
            b,
            &[ob(100, 12), ob(98, 7), ob(97, 20)],
            &mut out,
            touches,
        );
        // 101 вернулся — касание кончилось.
        tr.observe_frame_with_touches(
            D2 + 2000,
            b,
            &[ob(101, 4), ob(100, 12), ob(98, 7), ob(97, 20)],
            &mut out,
            touches,
        );
    };

    let mut carried = LevelTracker::with_carried_births(cfg_touch(), carry.clone());
    let mut t_carried = Vec::new();
    day2_frames(&mut carried, &mut t_carried);
    assert_eq!(t_carried.len(), 1);
    assert_eq!(t_carried[0].level_birth_ms, 1000, "возраст с прошлых суток");
    let births = carried.live_births();
    let birth_at = |key: (u8, i64)| -> i64 {
        births
            .iter()
            .find(|&&(k, _)| k == key)
            .map(|&(_, birth_ms)| birth_ms)
            .unwrap_or_else(|| panic!("ключ {key:?} не найден в переносе"))
    };
    assert_eq!(
        birth_at((side_key(b), 97)),
        1000,
        "97 стоял в первом кадре — перенос"
    );
    assert_eq!(
        birth_at((side_key(b), 98)),
        90_001_000,
        "98 появился позже — новорождённый"
    );

    let mut fresh = LevelTracker::new(cfg_touch());
    let mut t_fresh = Vec::new();
    day2_frames(&mut fresh, &mut t_fresh);
    assert_eq!(t_fresh.len(), 1);
    assert_eq!(
        t_fresh[0].level_birth_ms, 90_000_000,
        "без переноса — начало суток"
    );

    let warm = LevelsConfig {
        warmup_ms: HOUR_MS,
        ..cfg_touch()
    };
    let with_warmup = LevelTracker::with_carried_births(warm, carry);
    assert_eq!(
        with_warmup.carry.len(),
        0,
        "режим с прогревом перенос не принимает"
    );
}

/// Чистка `births` (W4а): цена, на которой уровень родился и умер один раз
/// и больше никогда не рождался заново, не должна вечно занимать место в
/// карте рождений — очередь, чей `count_prior_births` не зовут повторно,
/// никогда сама себя не тримит. `cleanup_births` обходит карту целиком не
/// чаще раза в `repeat_window_ms`; здесь окно короткое, чтобы кадры теста
/// оставались маленькими числами.
#[test]
fn births_map_forgets_a_price_that_never_rebirths_after_the_window() {
    let short_window = LevelsConfig {
        repeat_window_ms: 1_000,
        ..cfg()
    };
    let mut tr = LevelTracker::new(short_window);
    let mut out = Vec::new();

    // Рождение на 1000, немедленная смерть (тик исчезает из следующего
    // кадра — читается как размер ноль, ниже 20% максимума).
    tr.observe_frame(0, Side::Bid, &[ob(1000, 200)], &mut out);
    assert_eq!(tr.births.len(), 1, "запись рождения на 1000 появилась");
    out.clear();
    tr.observe_frame(10, Side::Bid, &[], &mut out);
    assert_eq!(out.len(), 1, "1000 умер");
    out.clear();

    // Кадр внутри старого окна: чистка ещё не должна снести свежую запись.
    tr.observe_frame(500, Side::Bid, &[ob(2000, 200)], &mut out);
    out.clear();
    assert!(
        tr.births.get(&(side_key(Side::Bid), 1000)).is_some(),
        "1000 моложе окна — чистка его ещё не трогает"
    );

    // Кадр за окном от последнего рождения на 1000 (t=0): следующее рождение
    // на другой цене запускает периодическую чистку, и запись 1000 уходит
    // вместе с опустевшей очередью, а не висит нулевой длины.
    tr.observe_frame(2_000, Side::Bid, &[ob(9999, 200)], &mut out);
    assert!(
        tr.births.get(&(side_key(Side::Bid), 1000)).is_none(),
        "запись истёкшей цены удалена целиком, а не оставлена пустой"
    );
    assert!(
        tr.births.get(&(side_key(Side::Bid), 9999)).is_some(),
        "свежее рождение по-прежнему учтено"
    );
}
