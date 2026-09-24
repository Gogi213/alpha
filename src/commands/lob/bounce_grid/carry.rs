//! Перенос круга через полночь (`--carry-root`, R6): окно переноса
//! (`carry_window_ns`), сутки-довесок (`next_day_utc`/`day_start_ns`),
//! события довеска в окне (`carry_events`), дописывание в поток событий и
//! флаг сверки (`extend_with_carry`) и кэш числа событий части
//! (`event_count_sidecar`/`file_stamp`/`cached_event_count`/`store_event_count`,
//! общий с `day_events`). Вынесено из `bounce_grid` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use hftbacktest::types::Event as HbtEvent;

use crate::commands::lob::backtest::{
    count_feed_events_until, feed_events_into_until, open_replay_feed, EntryTtl,
};
use crate::commands::lob::profiles::read_verify_marker;

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

/// Следующие сутки UTC `YYYY-MM-DD` — довесок смотрит ровно на них, не дальше
/// (круг, переживший ещё и вторую полночь, остаётся `incomplete`, как и
/// прежде — сетка замера ограничивает окно, не гоняется за произвольной
/// глубиной).
fn next_day_utc(day: &str) -> anyhow::Result<String> {
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
/// незачем (`count_feed_events_until`/`feed_events_into_until` уже говорят,
/// уткнулись ли).
pub(super) fn carry_events(parts: &[PathBuf], until_ns: i64) -> anyhow::Result<Vec<HbtEvent>> {
    let mut total = 0usize;
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        let (n, hit_bound) = count_feed_events_until(&mut feed, until_ns);
        total += n;
        if hit_bound {
            break;
        }
    }
    let mut events: Vec<HbtEvent> = Vec::with_capacity(total);
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        if feed_events_into_until(&mut feed, until_ns, &mut events) {
            break;
        }
    }
    Ok(events)
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
    events: &mut Vec<HbtEvent>,
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
    let carry_ev = carry_events(next_parts, until)?;
    eprintln!(
        "bounce-grid:   довесок {next_day}: событий {} за {:.1}с окна ({} частей)",
        carry_ev.len(),
        window_ns as f64 / 1e9,
        next_parts.len()
    );
    events.extend(carry_ev);
    let marker = carry_root.join(format!("verify-{symbol}.status"));
    let carry_unverified = !read_verify_marker(&marker);
    Ok((Some(boundary), carry_unverified))
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

/// Не смог записать — не беда: следующий прогон снова посчитает.
pub(super) fn store_event_count(path: &Path, n: usize) {
    if let Some((len, mtime)) = file_stamp(path) {
        let _ = std::fs::write(event_count_sidecar(path), format!("{len} {mtime} {n}\n"));
    }
}
