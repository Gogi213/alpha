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
