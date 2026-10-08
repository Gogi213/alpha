//! Перенос круга через полночь (`--carry-root`, R6): окно переноса
//! (`carry_window_ns`), сутки-довесок (`next_day_utc`/`day_start_ns`),
//! события довеска в окне (`carry_events`), дописывание в поток событий и
//! флаг сверки (`extend_with_carry`) и кэш числа событий части
//! (`event_count_sidecar`/`file_stamp`/`cached_event_count`/`store_event_count`,
//! общий с `day_events`). Вынесено из `bounce_grid` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::commands::lob::backtest::{
    count_feed_events_until, open_replay_feed, replay_compact_into_until, EntryTtl,
};
use crate::commands::lob::profiles::read_verify_marker;
use crate::lob::backtest::CompactEvent;

// ---------------------------------------------------------------------------
// Перенос круга через полночь (`--carry-root`): круг, ещё открытый на конце
// суток D, дочитывает выход по данным D+1 вместо `RoundOutcome::EndOfData`
// (измерено на кандидате 23.09: 35 из 189 круга истории, 8 из 43 записи —
// см. doc `BounceGridArgs::carry_root`).
// ---------------------------------------------------------------------------

/// Окно переноса, нс: сколько времени после полуночи D+1 ещё может
/// понадобиться, чтобы круг, открытый под конец суток D, дочитал свой выход.
/// Строится из чисел уже переданного прогона, не изобретённая константа:
/// наибольший потолок входа сетки (`--entry-ttl-secs`) плюс наибольший
/// `--deadline-secs` плюс небольшой запас на задержку исполнения (p95 RTT
/// тейкера — тот же параметр, что уже ограничивает выход рыночным ордером).
/// `EntryTtl::Touch` не добавляет времени: вход в этом режиме живёт не дольше
/// самого касания суток D (`touch.end_ms − touch.start_ms`), а касания —
/// собственные суток D, разбор их конца не выходит за пределы дня (F5,
/// `bounce_plan`). `EntryTtl::Wall` потолка не несёт (`entry_ttl_ns =
/// i64::MAX`, F5) — окно переноса с ним не построить, отказ, а не
/// изобретённое число.
pub(super) fn carry_window_ns(
    entry_ttls: &[EntryTtl],
    deadlines: &[u64],
    p95_taker_rtt_ns: i64,
) -> anyhow::Result<i64> {
    let mut max_entry_ttl_secs: i64 = 0;
    for ttl in entry_ttls {
        match ttl {
            EntryTtl::Touch => {}
            EntryTtl::Secs(secs) => max_entry_ttl_secs = max_entry_ttl_secs.max(*secs),
            EntryTtl::Wall => anyhow::bail!(
                "--carry-root: --entry-ttl-secs wall не ограничен по времени (F5, entry_ttl_ns = \
                 i64::MAX) — окно переноса не построить без числового потолка"
            ),
        }
    }
    let max_deadline_secs = deadlines.iter().copied().max().unwrap_or(0) as i64;
    Ok(max_entry_ttl_secs
        .saturating_add(max_deadline_secs)
        .saturating_mul(1_000_000_000)
        .saturating_add(p95_taker_rtt_ns))
}

/// Окно довеска до края суток (TK-048 C'): круг, открытый в `last_t0_ns`, живёт не дольше `horizon_ns`
/// (`carry_window_ns`), так что дальше `last_t0 + horizon` D+1 читать незачем. Длина от полуночи D+1
/// (`boundary_ns`): `clamp(last_t0 + horizon − boundary, 0, horizon)`; нет касаний, пропущенных наборами, — 0.
pub(super) fn carry_edge_window_ns(
    horizon_ns: i64,
    last_t0_ns: Option<i64>,
    boundary_ns: i64,
) -> i64 {
    match last_t0_ns {
        Some(t0) => t0
            .saturating_add(horizon_ns)
            .saturating_sub(boundary_ns)
            .clamp(0, horizon_ns),
        None => 0,
    }
}

/// Следующие сутки UTC `YYYY-MM-DD` — довесок смотрит ровно на них, не дальше
/// (круг, переживший ещё и вторую полночь, остаётся `incomplete`, как и
/// прежде — сетка замера ограничивает окно, не гоняется за произвольной
/// глубиной).
pub(super) fn next_day_utc(day: &str) -> anyhow::Result<String> {
    let d = chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .map_err(|e| anyhow::anyhow!("сутки {day:?}: ожидается YYYY-MM-DD ({e})"))?;
    let next = d
        .succ_opt()
        .ok_or_else(|| anyhow::anyhow!("сутки {day}: следующих суток не построить"))?;
    Ok(next.format("%Y-%m-%d").to_string())
}

/// Полночь UTC суток в наносекундах эпохи — граница переноса и колонка
/// `n_carried` (`forms.csv`): круг, чей `exit_ns ≥` эта граница, закрылся уже
/// на данных D+1.
pub(super) fn day_start_ns(day: &str) -> anyhow::Result<i64> {
    let d = chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .map_err(|e| anyhow::anyhow!("сутки {day:?}: ожидается YYYY-MM-DD ({e})"))?;
    d.and_hms_opt(0, 0, 0)
        .and_then(|dt| dt.and_utc().timestamp_nanos_opt())
        .ok_or_else(|| anyhow::anyhow!("сутки {day}: полночь не строится в нс"))
}

/// События суток-довеска D+1, ограниченные окном переноса (`until_ns`,
/// исключая) — не вся запись: тот же риск OOM, что решает двухпроходный
/// точный `Vec` `day_events` (её doc), только предел здесь не «конец файла»,
/// а окно. Части хронологичны (`session_parts_for`: день, потом часть) — как
/// только одна упёрлась в потолок, следующие начнутся ещё позже, читать их
/// незачем (`count_feed_events_until`/`replay_compact_into_until` уже говорят,
/// уткнулись ли).
#[cfg(test)]
pub(super) fn carry_events(parts: &[PathBuf], until_ns: i64) -> anyhow::Result<Vec<CompactEvent>> {
    let mut events = Vec::new();
    append_carry_events(parts, until_ns, &mut events)?;
    Ok(events)
}

/// Довесок в окне — прямо в конец `events` (Р10, T-22, 26.09): сперва счёт событий
/// довеска, затем `reserve_exact` и декод на месте. Раньше довесок собирался
/// отдельным `Vec` и дописывался `extend`: буфер суток выделен ровно по размеру
/// (`day_events`), поэтому `extend` растил его амортизированно — ёмкость до
/// удвоения, плюс копия довеска рядом на время `extend` (LSKUSDT 14.09: 146,6 млн
/// событий суток не влезли в потолок 12 ГБ). События и их порядок те же — меняется
/// только выделение памяти. Возвращает число дописанных событий.
pub(super) fn append_carry_events(
    parts: &[PathBuf],
    until_ns: i64,
    events: &mut Vec<CompactEvent>,
) -> anyhow::Result<usize> {
    let mut total = 0usize;
    let mut counted = Vec::new();
    for path in parts {
        let (n, hit_bound) = match cached_carry_count(path, until_ns) {
            Some(c) => c,
            None => {
                let mut feed = open_replay_feed(path)?;
                let c = count_feed_events_until(&mut feed, until_ns);
                store_carry_count(path, until_ns, c);
                c
            }
        };
        counted.push(path);
        total += n;
        if hit_bound {
            break;
        }
    }
    events.reserve_exact(total);
    let before = events.len();
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        if replay_compact_into_until(&mut feed, Some(until_ns), events) {
            break;
        }
    }
    let added = events.len() - before;
    if added != total {
        for path in counted {
            let mut feed = open_replay_feed(path)?;
            store_carry_count(path, until_ns, count_feed_events_until(&mut feed, until_ns));
        }
    }
    Ok(added)
}

/// Довесок конца суток `day` данными D+1 (см. `carry_window_ns`): дописывает
/// `events` в окне времени и отвечает, что писать в `forms.csv` — границу
/// полуночи D+1 (`n_carried`, `None` — довесок не применился: нет
/// `--carry-root`, нет частей D+1 или сутки последние в записи, прежнее
/// `incomplete`) и флаг «части довеска без сверки» (K1: используются в любом
/// случае — довесок только дочитывает уже открытые круги дня D, не заводит
/// новых сигналов, — но отсутствие маркера считается).
#[allow(clippy::too_many_arguments)]
pub(super) fn extend_with_carry(
    events: &mut Vec<CompactEvent>,
    day: &str,
    carry_root: Option<&Path>,
    carry_parts_by_day: &BTreeMap<String, Vec<PathBuf>>,
    symbol: &str,
    window_ns: i64,
) -> anyhow::Result<(Option<i64>, bool)> {
    let Some(carry_root) = carry_root else {
        return Ok((None, false));
    };
    let next_day = next_day_utc(day)?;
    let Some(next_parts) = carry_parts_by_day.get(&next_day) else {
        return Ok((None, false));
    };
    let boundary = day_start_ns(&next_day)?;
    let until = boundary.saturating_add(window_ns);
    // Нулевое окно (C': край суток не дотягивает до полуночи) — читать нечего.
    let n_carry = if window_ns > 0 {
        append_carry_events(next_parts, until, events)?
    } else {
        0
    };
    eprintln!(
        "bounce-grid:   довесок {next_day}: событий {} за {:.1}с окна ({} частей)",
        n_carry,
        window_ns as f64 / 1e9,
        next_parts.len()
    );
    let marker = carry_root.join(format!("verify-{symbol}.status"));
    let carry_unverified = !read_verify_marker(&marker);
    Ok((Some(boundary), carry_unverified))
}

/// То же, что `extend_with_carry`, без чтения довеска: граница полуночи D+1 и флаг сверки зависят
/// только от наличия частей D+1 и маркера (`ALPHA_SKIP_NOSIGNAL`, TK-049 — сутки без единого сигнала).
pub(super) fn carry_boundary(
    day: &str,
    carry_root: Option<&Path>,
    carry_parts_by_day: &BTreeMap<String, Vec<PathBuf>>,
    symbol: &str,
) -> anyhow::Result<(Option<i64>, bool)> {
    let Some(carry_root) = carry_root else {
        return Ok((None, false));
    };
    let next_day = next_day_utc(day)?;
    if !carry_parts_by_day.contains_key(&next_day) {
        return Ok((None, false));
    }
    let boundary = day_start_ns(&next_day)?;
    let marker = carry_root.join(format!("verify-{symbol}.status"));
    Ok((Some(boundary), !read_verify_marker(&marker)))
}

/// Сайдкар `<бинлог>.events` — число событий крейта в части: `размер мтайм число`.
/// Счёт — второй полный декод части (2.8 с из 8.2 с на сутках NEAR, замер
/// 20.09) ради буфера точного размера; число части не меняется, пока не
/// меняется сам файл, так что оно кэшируется рядом с ним по размеру и
/// мтайму. Суффикс не бинлоговый — резолверы частей сайдкар не видят.
fn event_count_sidecar(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(".events");
    PathBuf::from(name)
}

fn file_stamp(path: &Path) -> Option<(u64, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime = meta
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs();
    Some((meta.len(), mtime))
}

pub(super) fn cached_event_count(path: &Path) -> Option<usize> {
    let (len, mtime) = file_stamp(path)?;
    let text = std::fs::read_to_string(event_count_sidecar(path)).ok()?;
    let mut it = text.split_whitespace();
    let (l, m, n) = (
        it.next()?.parse::<u64>().ok()?,
        it.next()?.parse::<u64>().ok()?,
        it.next()?.parse::<usize>().ok()?,
    );
    (l == len && m == mtime).then_some(n)
}

fn carry_count_sidecar(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(".carry-events");
    PathBuf::from(name)
}

/// Счёт довеска окна `until_ns` по части: сайдкар `размер мтайм until n hit`
/// (одна строка — последнее окно); чужое окно или иной файл — промах.
pub(super) fn cached_carry_count(path: &Path, until_ns: i64) -> Option<(usize, bool)> {
    let (len, mtime) = file_stamp(path)?;
    let text = std::fs::read_to_string(carry_count_sidecar(path)).ok()?;
    let mut it = text.split_whitespace();
    let (l, m, u, n, h) = (
        it.next()?.parse::<u64>().ok()?,
        it.next()?.parse::<u64>().ok()?,
        it.next()?.parse::<i64>().ok()?,
        it.next()?.parse::<usize>().ok()?,
        it.next()?.parse::<u8>().ok()?,
    );
    (l == len && m == mtime && u == until_ns).then_some((n, h == 1))
}

pub(super) fn store_carry_count(path: &Path, until_ns: i64, (n, hit): (usize, bool)) {
    if let Some((len, mtime)) = file_stamp(path) {
        let _ = std::fs::write(
            carry_count_sidecar(path),
            format!(
                "{len} {mtime} {until_ns} {n} {}
",
                u8::from(hit)
            ),
        );
    }
}

/// Не смог записать — не беда: следующий прогон снова посчитает.
pub(super) fn store_event_count(path: &Path, n: usize) {
    if let Some((len, mtime)) = file_stamp(path) {
        let _ = std::fs::write(event_count_sidecar(path), format!("{len} {mtime} {n}\n"));
    }
}
