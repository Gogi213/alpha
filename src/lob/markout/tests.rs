use super::*;
use crate::book::Side;

/// Линейный обход — прежняя реализация `base_before`/`future_asof`
/// (до таска 33); двоичный поиск обязан давать ровно то же на любом
/// неубывающем срезе, включая повторы меток и края.
fn base_before_linear(mids: &[MidSample], death_ms: i64) -> Option<(i64, i64)> {
    let mut found = None;
    for s in mids {
        if s.ts_ms < death_ms {
            found = Some((s.ts_ms, mid_double_tick(s.bid_tick, s.ask_tick)));
        } else {
            break;
        }
    }
    found
}

fn future_asof_linear(mids: &[MidSample], base_ts: i64, horizon_ms: i64) -> Option<i64> {
    let target = base_ts.saturating_add(horizon_ms);
    let mut found = None;
    for s in mids {
        if s.ts_ms <= target {
            found = Some(mid_double_tick(s.bid_tick, s.ask_tick));
        } else {
            break;
        }
    }
    found
}

#[test]
fn binary_search_matches_the_linear_scan_on_ties_and_edges() {
    let ts = [0i64, 5, 5, 5, 7, 10, 10, 12];
    let mids: Vec<MidSample> = ts
        .iter()
        .enumerate()
        .map(|(i, &t)| {
            let i = i64::try_from(i).unwrap();
            MidSample {
                ts_ms: t,
                bid_tick: 100 + i,
                ask_tick: 101 + i,
            }
        })
        .collect();
    for q in -1..=14 {
        assert_eq!(
            base_before(&mids, q),
            base_before_linear(&mids, q),
            "base_before на death_ms={q}"
        );
        for h in [0i64, 1, 2, 5, 100] {
            assert_eq!(
                future_asof(&mids, q, h),
                future_asof_linear(&mids, q, h),
                "future_asof на base={q}, h={h}"
            );
        }
    }
    assert_eq!(base_before(&[], 5), None);
    assert_eq!(future_asof(&[], 5, 1), None);
    assert_eq!(sample_asof(&mids, 5, 0).map(|s| s.bid_tick), Some(103));
}

fn sample(ts_ms: i64, mid2x: i64) -> MidSample {
    let bid_tick = mid2x / 2;
    let ask_tick = mid2x - bid_tick;
    MidSample {
        ts_ms,
        bid_tick,
        ask_tick,
    }
}

/// A8.6 (2026-09-18): скачок часов (NTP-шаг). Контракт модуля — срезы идут
/// **неубывающим** временем; этот тест фиксирует, что делает читатель, когда
/// контракт нарушен, вместо молчаливого «починим потом». Серия
/// `1000, 1500, 1200, 1700` (шаг назад на третьем срезе): `partition_point`
/// не проверяет порядок и останавливается на **первом** срезе позже цели —
/// значит всё, что лежит за перелётом, недостижимо.
///
/// Поведение не «чинится» намеренно: при расхождении часов неизвестно, какая
/// метка истина, и любое правило было бы догадкой (правило «изобретённое число
/// запрещено»). На записи коллектора этого не случается: середина строится по
/// времени биржи (`cts` кадра), а локальная метка идёт только в артефакты
/// сессии (`gaps.csv`, `clock.csv`, `session.json`).
#[test]
fn a_non_monotonic_series_is_backed_by_documented_behaviour_not_a_guess() {
    let mids = vec![
        sample(1000, 2000),
        sample(1500, 2002),
        // Шаг назад: метка меньше предыдущей.
        sample(1200, 2010),
        sample(1700, 2004),
    ];
    // Цель 1600. Предикат `ts <= 1600` по позициям: T, T, T, F — он
    // упорядочен, поэтому граница определена и равна 3, то есть берётся срез
    // **индекса 2** с меткой 1200. «Последний по времени ≤ 1600» — это 1500,
    // и он не возвращается: вот она, ловушка немонотонной серии.
    assert_eq!(sample_asof(&mids, 0, 1600).map(|s| s.ts_ms), Some(1200));
    assert_eq!(future_asof(&mids, 0, 1600), Some(2010));
    assert_eq!(
        base_before(&mids, 1600).map(|(ts, _)| ts),
        Some(1200),
        "та же граница, что у sample_asof: предикат `ts < 1600` тоже упорядочен"
    );
    // Цель 1300. Предикат по позициям: T, F, T, F — он **не** упорядочен, и
    // `partition_point` на таком входе не обещает ничего, кроме существующего
    // индекса (документация std требует partitioned-предиката). Проверяется
    // ровно то, что гарантировано: ответ — срез этой же серии, без паники и
    // без выдуманного «нулевого» среза.
    for target in [1250, 1300, 1500, 1699] {
        let got = sample_asof(&mids, 0, target).map(|s| s.ts_ms);
        assert!(
            got.is_some_and(|ts| mids.iter().any(|m| m.ts_ms == ts)),
            "на цели {target} обязан вернуться срез серии, а не выдумка: {got:?}"
        );
    }
    // До первого среза и после последнего поведение обычное: порядок на это
    // не влияет.
    assert_eq!(sample_asof(&mids, 0, 800), None);
    assert_eq!(sample_asof(&mids, 0, 5000).map(|s| s.ts_ms), Some(1700));
}

fn level(side: Side, death_ms: i64) -> LevelRecord {
    LevelRecord {
        side,
        price_tick: 1000,
        birth_ms: 0,
        death_ms,
        lifetime_ms: death_ms,
        size_max: 200,
        time_to_max_ms: 0,
        size_monotonic: true,
        repeat_count: 0,
        repriced: false,
        death: crate::lob::levels::DeathKind::BelowFraction,
        traded_lots: 0,
        rpi_lots: 0,
    }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// Линейный тренд: середина растёт на 2 единицы удвоенной шкалы за
/// 100 мс от базы 20000. Ожидаемые `m` для стороны аска:
/// 1.0, 10.0, 100.0, 600.0 bps — точное значение формулы.
#[test]
fn linear_trend_gives_exact_markouts() {
    let mids = vec![
        sample(0, 20_000),
        sample(100, 20_002),
        sample(1_000, 20_020),
        sample(10_000, 20_200),
        sample(60_000, 21_200),
    ];
    let lv = level(Side::Ask, 50);
    let got = markouts_for_level(&lv, &mids);
    let want = [1.0, 10.0, 100.0, 600.0];
    for (i, h) in HORIZONS_MS.iter().enumerate() {
        let v = got[i].expect("будущее есть на каждом горизонте");
        assert!(close(v, want[i]), "горизонт {h}: got {v}, want {}", want[i]);
    }
    assert_eq!(HORIZONS_MS, [100, 1_000, 10_000, 60_000]);
}

/// Для съеденного уровня база обязана браться до сделок: срез ровно
/// в момент смерти игнорируется строгим неравенством.
#[test]
fn eaten_base_is_taken_before_the_killing_trades() {
    let mids = vec![sample(900, 20_000), sample(1_000, 20_200)];
    let base = base_before(&mids, 1_000).expect("база обязана найтись");
    assert_eq!(base, (900, 20_000), "срез в момент смерти — не база");
    let m_from_base = markout_bps(Side::Ask, base.1, 20_400).unwrap();
    let m_from_death = markout_bps(Side::Ask, 20_200, 20_400).unwrap();
    assert!(close(m_from_base, 400.0 / 20_000.0 * 10_000.0));
    assert!(
        !close(m_from_base, m_from_death),
        "базы обязаны различаться"
    );
    let lv = level(Side::Ask, 1_000);
    let fut = future_asof(&mids, base.0, 100).unwrap();
    assert_eq!(fut, 20_200);
    let _ = markouts_for_level(&lv, &mids);
}

/// Зеркальное движение: бид вниз и аск вверх на ту же величину дают
/// противоположную сырую доходность и одинаковое подписанное `m`.
#[test]
fn mirrored_moves_give_opposite_raw_and_equal_markout() {
    let base2x = 20_000;
    let bid_fut = 19_800;
    let ask_fut = 20_200;
    let raw_bid = raw_return_bps(base2x, bid_fut).unwrap();
    let raw_ask = raw_return_bps(base2x, ask_fut).unwrap();
    assert!(close(raw_bid, -100.0));
    assert!(close(raw_ask, 100.0));
    assert!(
        raw_bid.signum() != raw_ask.signum(),
        "сырой знак противоположен"
    );
    let m_bid = markout_bps(Side::Bid, base2x, bid_fut).unwrap();
    let m_ask = markout_bps(Side::Ask, base2x, ask_fut).unwrap();
    assert!(close(m_bid, 100.0));
    assert!(close(m_ask, 100.0));
    assert!(close(m_bid, m_ask), "подписанное m обязано совпасть");
}

/// Граница модулей в духе шага 1.1: чистая логика не знает про
/// транспорт и часы. Проверка — грепом по собственному исходнику.
/// Вещественное число разрешено только формулой, поэтому его здесь нет
/// среди запрещённых, в отличие от списка уровней.
#[test]
fn module_stays_detached_from_transport_and_clocks() {
    const SRC: &str = include_str!("../markout.rs");
    let banned = [
        concat!("by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("std::", "time"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}

fn touch(side: Side, start_ms: i64) -> TouchRecord {
    TouchRecord {
        side,
        price_tick: 1000,
        touch_index: 0,
        start_ms,
        end_ms: start_ms + 500,
        duration_ms: 500,
        level_birth_ms: 0,
        size_at_touch: 200,
        size_max_before: 200,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 3,
        ended_by_death: false,
        stack_levels: 1,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        strength_e2: [-1, -1, -1],
        strength_held_e2: [-1, -1, -1, -1],
        repeat_count: 0,
    }
}

/// В-45 (2): горизонт не длиннее касания — `within_touch`, граница
/// включительна (`HORIZONS_MS[i] ≤ duration_ms`); `markouts_for_touch_outside`
/// гасит такие горизонты в `None`, остальные — как у `markouts_for_touch`.
#[test]
fn horizons_not_longer_than_the_touch_are_within_it_and_dropped_from_outside_markouts() {
    assert_eq!(within_touch(0), [false; 4]);
    assert_eq!(within_touch(99), [false; 4]);
    assert_eq!(
        within_touch(100),
        [true, false, false, false],
        "граница включительна"
    );
    assert_eq!(within_touch(1_000), [true, true, false, false]);
    assert_eq!(within_touch(59_999), [true, true, true, false]);
    assert_eq!(within_touch(60_000), [true; 4]);

    let mids: Vec<MidSample> = (0..=70).map(|i| sample(i * 1_000, 2_002 + i)).collect();
    let mut t = touch(Side::Bid, 5_000);
    t.duration_ms = 10_000;
    t.end_ms = 15_000;
    let all = markouts_for_touch(&t, &mids);
    let outside = markouts_for_touch_outside(&t, &mids);
    assert!(all.iter().all(Option::is_some), "{all:?}");
    assert_eq!(outside, [None, None, None, all[3]]);
}

/// Критерий приёмки таска 35: середина после касания выше — бид-касание
/// даёт `m > 0` (знак «в сторону отскока», противоположный смерти: тот же
/// тренд для смерти бида даёт `m < 0`); аск на том же тренде — `m < 0`.
/// База — последний срез строго до `start_ms`, как у смерти; будущее —
/// как есть на `t0 + h` — точные значения линейного тренда.
#[test]
fn a_bid_touch_followed_by_a_rising_mid_has_positive_markout() {
    let mids = vec![
        sample(0, 20_000),
        sample(100, 20_002),
        sample(1_000, 20_020),
        sample(10_000, 20_200),
        sample(60_000, 21_200),
    ];
    let bid = touch(Side::Bid, 50);
    let got = markouts_for_touch(&bid, &mids);
    let want = [1.0, 10.0, 100.0, 600.0];
    for (i, h) in HORIZONS_MS.iter().enumerate() {
        let v = got[i].expect("будущее есть на каждом горизонте");
        assert!(v > 0.0 && close(v, want[i]), "горизонт {h}: got {v}");
    }
    let death = markouts_for_level(&level(Side::Bid, 50), &mids);
    for (t, d) in got.iter().zip(death) {
        assert!(close(t.unwrap(), -d.unwrap()), "касание — минус смерть");
    }
    let ask = markouts_for_touch(&touch(Side::Ask, 50), &mids);
    for v in ask {
        assert!(v.unwrap() < 0.0, "аск на росте — против отскока");
    }
    assert_eq!(
        touch_markout_bps(Side::Bid, 0, 1),
        None,
        "неположительная база"
    );
    assert_eq!(
        markouts_for_touch(&touch(Side::Bid, -1), &mids),
        [None; 4],
        "до первого среза базы нет"
    );
}

/// В-43: база касания — срез как есть на `start_ms`, включая шаг самого
/// кадра касания. Как на живом потоке (`feed_frames_multi` пишет срез после
/// кадра), середина шагнула вниз на бид ровно в `start_ms` и дальше стоит:
/// `m == 0` на всех горизонтах. Прежняя база «строго до `start_ms`» дала бы
/// −1 тик на всех горизонтах — шаг кадра, не отскок (R-C).
#[test]
fn the_touch_base_is_the_mid_as_of_start_ms_including_the_step_onto_the_level() {
    let mids = vec![
        sample(0, 20_000),
        sample(1_000, 19_998),
        sample(1_100, 19_998),
        sample(2_000, 19_998),
        sample(11_000, 19_998),
        sample(61_000, 19_998),
    ];
    let bid = touch(Side::Bid, 1_000);
    assert_eq!(touch_base(&mids, 1_000), Some((1_000, 19_998)));
    for (h, v) in HORIZONS_MS.iter().zip(markouts_for_touch(&bid, &mids)) {
        assert_eq!(v, Some(0.0), "горизонт {h}: сдвига после касания нет");
    }
    let (old_ts, old2x) = base_before(&mids, 1_000).expect("срез строго до есть");
    assert_eq!((old_ts, old2x), (0, 20_000));
    let stale = touch_markout_bps(Side::Bid, old2x, 19_998).expect("база положительна");
    assert!(stale < 0.0, "прежняя база несла шаг кадра: {stale}");
    // Подход — от среза как есть на `start_ms − N` до той же базы: шаг вниз
    // на бид за секунду до касания — плюс; десяти секунд до касания в этой
    // записи нет — окна нет, а не ноль.
    let [a1, a10] = approaches_for_touch(&bid, &mids);
    assert!(a1.expect("секунда до касания есть") > 0.0);
    assert_eq!(a10, None);
}

/// Критерий приёмки таска 35: середина падала на бид перед касанием —
/// `approach_1s > 0` (знак «к уровню»); за 10 с — тоже, если падала и там;
/// раньше первого среза окна нет — `None`, а не ноль. Аск на том же
/// падении — отрицательный подход: цена уходила от него.
#[test]
fn a_mid_falling_onto_the_bid_gives_positive_approach() {
    // Падение 20 единиц удвоенной шкалы за секунду, 200 за десять.
    let mids = vec![
        sample(0, 20_200),
        sample(9_000, 20_020),
        sample(10_000, 20_000),
        sample(10_500, 20_000),
    ];
    let bid = touch(Side::Bid, 10_001);
    let [a1, a10] = approaches_for_touch(&bid, &mids);
    let a1 = a1.expect("секунда до касания есть");
    let a10 = a10.expect("десять секунд до касания есть");
    assert!(
        a1 > 0.0 && close(a1, 20.0 / 20_020.0 * 10_000.0),
        "approach_1s = {a1}"
    );
    assert!(
        a10 > 0.0 && close(a10, 200.0 / 20_200.0 * 10_000.0),
        "approach_10s = {a10}"
    );
    let ask = approaches_for_touch(&touch(Side::Ask, 10_001), &mids);
    assert!(
        ask[0].unwrap() < 0.0 && ask[1].unwrap() < 0.0,
        "аск: цена уходила"
    );
    // Касание в 1 мс: база — первый срез (0), секунда до него — раньше
    // записи, окна нет.
    assert_eq!(
        approaches_for_touch(&touch(Side::Bid, 1), &mids),
        [None, None]
    );
    assert_eq!(APPROACH_MS, [HORIZONS_MS[1], HORIZONS_MS[2]]);
}

/// B3 (В-58 п. 4): длина горизонтов — проверяемая величина. Горизонт длиннее
/// суток неотличим от пустой выборки, поэтому это **отказ**, а не молчаливые
/// `None` во всех строках артефакта; нулевой и отрицательный — тоже отказ.
#[test]
fn horizons_longer_than_a_day_are_refused() {
    assert!(
        check_horizons(&HORIZONS_MS).is_ok(),
        "боевые четыре проходят"
    );
    assert!(
        check_horizons(&HORIZONS_LONG_MS).is_ok(),
        "длинные (10 мин/1 ч/2 ч) проходят"
    );
    let err = check_horizons(&[DAY_MS + 1]).unwrap_err();
    assert!(err.contains("длиннее суток"), "{err}");
    assert!(check_horizons(&[0]).is_err(), "нулевой горизонт — отказ");
    assert!(check_horizons(&[-1]).is_err(), "отрицательный — отказ");
}

/// B3: длинные markout'ы читают ровно 10 мин / 1 ч / 2 ч от базы касания и
/// молчат (`None`) там, где запись кончилась раньше горизонта — иначе «нет
/// данных» выглядело бы как нулевое движение цены.
#[test]
fn long_markouts_read_the_ten_minute_hour_and_two_hour_points() {
    let start = 1_000_000_i64;
    // Середина 2000 (то есть 1000.0) на старте и по точке на каждый горизонт:
    // +10 мин, +1 ч, +2 ч — каждая на тик выше (сдвиг 0.5 bps на 2000).
    let mids = vec![
        sample(start, 2_000),
        sample(start + HORIZONS_LONG_MS[0], 2_002),
        sample(start + HORIZONS_LONG_MS[1], 2_004),
        sample(start + HORIZONS_LONG_MS[2], 2_006),
    ];
    let m = long_markouts_for_touch(&touch(Side::Bid, start), &mids);
    assert_eq!(HORIZONS_LONG_MS, [600_000, 3_600_000, 7_200_000]);
    for (i, got) in m.iter().enumerate() {
        let expected = touch_markout_bps(Side::Bid, 2_000, 2_000 + 2 * (i as i64 + 1));
        assert!(
            (got.unwrap() - expected.unwrap()).abs() < 1e-9,
            "горизонт {i}: {got:?} против {expected:?}"
        );
    }

    // Базы нет вовсе (касание раньше первого среза) — все три `None`, как у
    // коротких горизонтов: молчаливого нуля здесь быть не должно.
    let late = vec![sample(start + 10_000, 2_000)];
    assert_eq!(
        long_markouts_for_touch(&touch(Side::Bid, start), &late),
        [None, None, None]
    );
}
