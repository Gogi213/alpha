//! Оси сетки профилей: корзины размера, времени жизни и расстояния, час
//! рождения уровня и срез середины «как есть» на горизонте. Чистые функции
//! без ввода-вывода — их же читает `lob dashboard` (через ре-экспорт из
//! `profiles.rs`), поэтому лежат отдельно от накопления и записи CSV.

use crate::lob::levels::LevelRecord;
use crate::lob::markout::{base_before, raw_return_bps, MidSample};
use crate::lob::shortlist::{DISTANCE_BOUNDS_BPS, DISTANCE_LABELS, LIFETIME_LABELS, SIZE_LABELS};

// ---------------------------------------------------------------------------
// Числовые границы осей «размер» и «время жизни» — метки берутся из
// `shortlist`, числа фиксированы здесь той же спекой («Профиль», история 14).
// ---------------------------------------------------------------------------

/// Кратности порога `H3`: `[1,2)`, `[2,4)`, `[4,∞)` (`shortlist::SIZE_LABELS`).
pub(super) const SIZE_BOUNDS: [(f64, f64); 3] = [(1.0, 2.0), (2.0, 4.0), (4.0, f64::INFINITY)];

/// Время жизни в мс: `[0,1с)`, `[1,10с)`, `[10с,∞)` (`shortlist::LIFETIME_LABELS`).
pub(super) const LIFETIME_BOUNDS_MS: [(i64, i64); 3] =
    [(0, 1_000), (1_000, 10_000), (10_000, i64::MAX)];

pub(crate) fn size_bucket(ratio: f64) -> Option<&'static str> {
    SIZE_BOUNDS
        .iter()
        .zip(SIZE_LABELS.iter())
        .find(|((lo, hi), _)| ratio >= *lo && ratio < *hi)
        .map(|(_, label)| *label)
}

pub(crate) fn lifetime_bucket(lifetime_ms: i64) -> Option<&'static str> {
    LIFETIME_BOUNDS_MS
        .iter()
        .zip(LIFETIME_LABELS.iter())
        .find(|((lo, hi), _)| lifetime_ms >= *lo && lifetime_ms < *hi)
        .map(|(_, label)| *label)
}

pub(crate) fn distance_bucket(dist_bps: f64) -> Option<&'static str> {
    DISTANCE_BOUNDS_BPS
        .iter()
        .zip(DISTANCE_LABELS.iter())
        .find(|((lo, hi), _)| dist_bps >= *lo && dist_bps < *hi)
        .map(|(_, label)| *label)
}

/// Расстояние до середины в bps на срезе строго до рождения уровня — тот же
/// приём, что `markout::base_before` использует для смерти (`death_ms`),
/// сдвинутый на рождение (`birth_ms + 1`, чтобы включить срез ровно в момент
/// рождения). `None` — до рождения не было ни одного среза книги.
pub(crate) fn distance_bps_at_birth(mids: &[MidSample], rec: &LevelRecord) -> Option<f64> {
    let (_, mid2x) = base_before(mids, rec.birth_ms.saturating_add(1))?;
    raw_return_bps(mid2x, rec.price_tick.saturating_mul(2)).map(f64::abs)
}

/// Час UTC метки времени биржи в миллисекундах — ось «час» профиля (В-36).
///
/// Целочисленно (`div_euclid`/`rem_euclid`), как всё время в проекте (A1),
/// и тотально: `rem_euclid` даёт `[0, 24)` при любом знаке аргумента, так
/// что метка до эпохи (в данных не бывает, но арифметика не имеет права
/// зависеть от этого) не даёт отрицательного часа. Часовых поясов здесь нет
/// вовсе: биржа отдаёт UTC, и весь проект живёт в UTC.
pub(super) fn hour_utc_of_ms(ts_ms: i64) -> u32 {
    const MS_PER_HOUR: i64 = 3_600_000;
    const HOURS_PER_DAY: i64 = 24;
    let hour = ts_ms.div_euclid(MS_PER_HOUR).rem_euclid(HOURS_PER_DAY);
    u32::try_from(hour).expect("rem_euclid(24) лежит в [0, 24)")
}

/// Срез середины как есть на `base_ts + horizon_ms` — тот же поиск, что
/// `markout::future_asof`, но возвращает весь срез (нужны обе стороны для
/// спреда выхода `costs::Observation::spread_ticks_exit`), а не только
/// удвоенную середину.
pub(super) fn mid_sample_asof(
    mids: &[MidSample],
    base_ts: i64,
    horizon_ms: i64,
) -> Option<MidSample> {
    let target = base_ts.saturating_add(horizon_ms);
    let mut found: Option<MidSample> = None;
    for s in mids {
        if s.ts_ms <= target {
            found = Some(*s);
        } else {
            break;
        }
    }
    found
}
