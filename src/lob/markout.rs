//! Markout уровня по Decisions 11 и 14 (план, §2.1).
//!
//! База — середина по последнему обновлению книги строго до начала
//! исчезновения уровня: последний срез с меткой строго меньше `death_ms`.
//! Середина в момент исчезновения для съеденных уровней сдвинута теми
//! сделками, что уровень съели, и встраивает результат в измеритель.
//!
//! Полярность: `m = s * (mid_f - mid_b) / mid_b * 10^4`, где `s = -1` для
//! стороны бида и `+1` для стороны аска. Положительное `m` означает, что
//! сигнал заработал в подразумеваемую сторону.
//!
//! Целые до последнего, как в книге (A7): тики и миллисекунды — `i64`,
//! середина держится удвоенной суммой `bid + ask` (ровно держит половину
//! тика без деления), вещественное число появляется один раз — при делении
//! в формуле базисных пунктов.
//!
//! История подаётся вызывающим срезом в неубывающем времени; этот модуль
//! не хранит состояния и не выделяет память.

use crate::book::Side;
use crate::lob::levels::LevelRecord;

/// Горизонты замера, мс: 100 мс, 1 с, 10 с, 60 с.
pub const HORIZONS_MS: [i64; 4] = [100, 1_000, 10_000, 60_000];

/// Множитель перевода доли в базисные пункты.
const BPS_SCALE: f64 = 10_000.0;

/// Один срез середины: лучшие тики обеих сторон на метку обновления книги.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MidSample {
    /// Метка обновления книги, мс.
    pub ts_ms: i64,
    /// Лучший бид в тиках.
    pub bid_tick: i64,
    /// Лучший аск в тиках.
    pub ask_tick: i64,
}

/// Удвоенная середина `bid + ask`: ровно держит половину тика без деления.
/// Насыщает вместо переполнения: тики вне диапазона всё равно не образуют
/// осмысленной середины.
pub fn mid_double_tick(bid_tick: i64, ask_tick: i64) -> i64 {
    bid_tick.saturating_add(ask_tick)
}

/// База по Decision 11: последний срез строго до смерти уровня.
/// Возвращает пару (метка базы, удвоенная середина базы).
pub fn base_before(mids: &[MidSample], death_ms: i64) -> Option<(i64, i64)> {
    let mut found: Option<(i64, i64)> = None;
    for s in mids {
        if s.ts_ms < death_ms {
            found = Some((s.ts_ms, mid_double_tick(s.bid_tick, s.ask_tick)));
        } else {
            break;
        }
    }
    found
}

/// Середина на горизонте как есть на момент `base_ts + horizon_ms`:
/// последний срез с меткой не позже цели, без заглядывания вперёд.
pub fn future_asof(mids: &[MidSample], base_ts: i64, horizon_ms: i64) -> Option<i64> {
    let target = base_ts.saturating_add(horizon_ms);
    let mut found: Option<i64> = None;
    for s in mids {
        if s.ts_ms <= target {
            found = Some(mid_double_tick(s.bid_tick, s.ask_tick));
        } else {
            break;
        }
    }
    found
}

/// Сырая доходность середины в bps без полярности по стороне:
/// `(fut - base) / base * 10^4`. `None` при неположительной базе.
/// Касты точные: разности цен в e9 — порядков 1e13 максимум, база положительна
/// и того же масштаба; оба далеко от 2^53.
#[allow(clippy::cast_precision_loss)]
pub fn raw_return_bps(base2x: i64, fut2x: i64) -> Option<f64> {
    if base2x <= 0 {
        return None;
    }
    let diff = (fut2x as i128 - base2x as i128) as f64;
    Some(diff / base2x as f64 * BPS_SCALE)
}

/// Markout по Decision 14: сырая доходность со знаком стороны.
/// Бид подразумевает шорт (`s = -1`), аск — лонг (`s = +1`).
/// `None` при неположительной базе. Целые до деления: разность и знак
/// складываются в целых, деление одно. Касты — та же точность, что выше.
#[allow(clippy::cast_precision_loss)]
pub fn markout_bps(side: Side, base2x: i64, fut2x: i64) -> Option<f64> {
    if base2x <= 0 {
        return None;
    }
    let sigma: i128 = match side {
        Side::Bid => -1,
        Side::Ask => 1,
    };
    let signed_diff = sigma * (fut2x as i128 - base2x as i128);
    Some(signed_diff as f64 / base2x as f64 * BPS_SCALE)
}

/// Markout уровня на всех горизонтах: база строго до смерти, будущее —
/// как есть на `t0 + h`, где `t0` — метка базы. Нет базы или нет будущего
/// на горизонте — `None` на этом горизонте, а не ноль: отсутствие данных
/// не есть нулевой сдвиг.
pub fn markouts_for_level(level: &LevelRecord, mids: &[MidSample]) -> [Option<f64>; 4] {
    let Some((base_ts, base2x)) = base_before(mids, level.death_ms) else {
        return [None, None, None, None];
    };
    std::array::from_fn(|i| {
        // Индекс доказуемо в границах (`from_fn` идёт ровно по `0..4` при
        // длине 4), но `get` здесь бесплатен и тотален: недостижимая ветвь
        // даёт пропуск горизонта, а не панику.
        let h = *HORIZONS_MS.get(i)?;
        let fut2x = future_asof(mids, base_ts, h)?;
        markout_bps(level.side, base2x, fut2x)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::book::Side;

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
        const SRC: &str = include_str!("markout.rs");
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
}
