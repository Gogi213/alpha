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
    // Срезы идут в неубывающем времени (контракт модуля), поэтому «последний
    // строго до» — граница `partition_point`, а не линейный обход: у суточной
    // записи миллионы срезов и десятки тысяч уровней, линейный поиск на
    // каждый уровень делал реплей квадратичным (таск 33). Результат тот же,
    // что у обхода с `break` на первом `ts_ms >= death_ms`.
    let n = mids.partition_point(|s| s.ts_ms < death_ms);
    let s = mids.get(n.checked_sub(1)?)?;
    Some((s.ts_ms, mid_double_tick(s.bid_tick, s.ask_tick)))
}

/// Середина на горизонте как есть на момент `base_ts + horizon_ms`:
/// последний срез с меткой не позже цели, без заглядывания вперёд.
pub fn future_asof(mids: &[MidSample], base_ts: i64, horizon_ms: i64) -> Option<i64> {
    sample_asof(mids, base_ts, horizon_ms).map(|s| mid_double_tick(s.bid_tick, s.ask_tick))
}

/// Тот же срез, что `future_asof`, целиком — нужен там, где на горизонте
/// важна не только середина, но и спред (`costs::observation_at`).
/// Двоичный поиск по неубывающему времени, как у `base_before`.
pub fn sample_asof(mids: &[MidSample], base_ts: i64, horizon_ms: i64) -> Option<MidSample> {
    let target = base_ts.saturating_add(horizon_ms);
    let n = mids.partition_point(|s| s.ts_ms <= target);
    mids.get(n.checked_sub(1)?).copied()
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
mod tests;
