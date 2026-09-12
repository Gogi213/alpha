//! Общий реплей: суточные файлы символа → книга → записи уровней и срезы
//! середины (`replay_symbol`, `replay_symbol_over_configs`) плюс `day_tallies`
//! для `watch`/`markout`. Отдельно от `mod.rs`: это единственная бизнес-склейка,
//! которую делят `levels`, `markout`, `watch`, `pilot`, `profiles`, `dashboard`.

use std::path::Path;

use crate::binlog::{Reader, Record};
use crate::book::{Book, Side};
use crate::bybit::verify::{is_trade_ev, FileReplayer};
use crate::bybit::verify_sidecar::{read_verify_rows, verify_csv_path, VerifyVerdict};
use crate::lob::levels::{
    LevelObs, LevelRecord, LevelTracker, LevelsConfig, LiveLevel, TouchRecord, TradeHit,
};
use crate::lob::markout::MidSample;
use crate::lob::watch::{tally_day, DayTally};
use hftbacktest::types::LOCAL_BUY_TRADE_EVENT;

use super::parts::{day_of_filename, list_symbol_binlogs};

// ---------------------------------------------------------------------------
// Общий реплей: суточные файлы символа → записи уровней и срезы середины.
// ---------------------------------------------------------------------------

/// Одни сутки UTC после реплея: записи уровней, касания живых уровней
/// (таск 35) и срезы середины.
pub(crate) struct ReplayDay {
    pub(crate) day: String,
    pub(crate) records: Vec<LevelRecord>,
    pub(crate) touches: Vec<TouchRecord>,
    pub(crate) mids: Vec<MidSample>,
}

/// Итог реплея символа: сутки плюс счётчики для замера GC (байт на запись),
/// шаг цены/лота из заголовка бинлога и живые уровни на последнем кадре
/// (таск 33: дашборд показывает «плотности сейчас» — то, что ещё не умерло
/// и потому не попало в `records`).
pub(crate) struct ReplayStats {
    pub(crate) days: Vec<ReplayDay>,
    pub(crate) bytes: u64,
    pub(crate) records: u64,
    pub(crate) tick_e9: i64,
    pub(crate) step_e9: i64,
    pub(crate) open: Vec<LiveLevel>,
}

/// Рабочее состояние одних суток при нескольких конфигурациях `H3` разом
/// (таск 18, В-30/D05: сетка `pilot::K_GRID` — пять порогов за один
/// декодированный проход, не пять реплеев): книга и конвертер пересоздаются
/// на каждый файл (каждый начинается со снапшота), трекеры живут все части
/// суток — окно `repeat_count` и прогрев считаются по суткам. `mids` один на
/// сутки — срез середины не зависит от конфигурации `H3`, только `records`
/// и трекеры множатся по числу конфигураций. `replay_symbol` — частный
/// случай с одной конфигурацией, реализован через `replay_symbol_over_configs`.
struct DayWork {
    day: String,
    records: Vec<Vec<LevelRecord>>,
    touches: Vec<Vec<TouchRecord>>,
    mids: Vec<MidSample>,
    trackers: Vec<LevelTracker>,
}

/// Трейд записи в трейд трекера. Отображение повторяет контракт писателя
/// (`record.rs::stage_trade`: `ev` из стороны агрессора, `ival = 1` —
/// блочная) и читателя (`verify.rs`: блочность из `ival`); своей трактовки
/// битов здесь нет.
pub(crate) fn trade_hit_from_record(rec: &Record) -> Option<TradeHit> {
    if !is_trade_ev(rec.ev) {
        return None;
    }
    Some(TradeHit {
        tick: rec.price_ticks,
        lots: rec.qty_lots,
        aggressor_is_buy: rec.ev == LOCAL_BUY_TRADE_EVENT,
        block: rec.ival != 0,
        exch_ms: rec.exch_ts_ns / 1_000_000,
    })
}

/// Применяет обновление к книге и кормит трекер кадром обеих сторон плюс
/// срезом середины. Вызывается только после успешного `apply`. Одна
/// конфигурация — частный случай `feed_frames_multi` (таск 18), но
/// оставлена отдельной функцией: `profiles.rs::run_profiles_with_fill_model`
/// (вне зоны таска 18, «не трогать») зовёт её напрямую с одним трекером,
/// вторая сигнатура (`&mut [LevelTracker]`) поменяла бы её вызов. Касания
/// (таск 35) здесь не собираются — `observe_frame` их отбрасывает; читатель
/// касаний — `replay_symbol_over_configs` через `feed_frames_multi`.
pub(crate) fn feed_frames(
    book: &Book,
    tracker: &mut LevelTracker,
    ts_ms: i64,
    out: &mut Vec<LevelRecord>,
    mids: &mut Vec<MidSample>,
) {
    for side in [Side::Bid, Side::Ask] {
        let obs: Vec<LevelObs> = book
            .levels(side)
            .enumerate()
            .map(|(i, (tick, lots))| LevelObs {
                tick,
                size_lots: lots,
                in_top50: i < 50,
            })
            .collect();
        tracker.observe_frame(ts_ms, side, &obs, out);
    }
    if let (Some(bid), Some(ask)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
        mids.push(MidSample {
            ts_ms,
            bid_tick: bid,
            ask_tick: ask,
        });
    }
}

/// Применяет обновление к книге и кормит **все** трекеры кадром обеих
/// сторон плюс общим срезом середины (таск 18: `LevelObs` не зависит от
/// порога `H3`, поэтому строится один раз на кадр и раздаётся всем
/// трекерам — не по разу на конфигурацию). Касания (таск 35) — в
/// `touches`, по вектору на конфигурацию, как `out`. Вызывается только
/// после успешного `apply`.
fn feed_frames_multi(
    book: &Book,
    trackers: &mut [LevelTracker],
    ts_ms: i64,
    out: &mut [Vec<LevelRecord>],
    touches: &mut [Vec<TouchRecord>],
    mids: &mut Vec<MidSample>,
) {
    debug_assert_eq!(
        trackers.len(),
        out.len(),
        "трекер и выход обязаны идти парой"
    );
    debug_assert_eq!(
        trackers.len(),
        touches.len(),
        "трекер и выход касаний обязаны идти парой"
    );
    for side in [Side::Bid, Side::Ask] {
        let obs: Vec<LevelObs> = book
            .levels(side)
            .enumerate()
            .map(|(i, (tick, lots))| LevelObs {
                tick,
                size_lots: lots,
                in_top50: i < 50,
            })
            .collect();
        for ((tracker, out), touches) in trackers
            .iter_mut()
            .zip(out.iter_mut())
            .zip(touches.iter_mut())
        {
            tracker.observe_frame_with_touches(ts_ms, side, &obs, out, touches);
        }
    }
    if let (Some(bid), Some(ask)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
        mids.push(MidSample {
            ts_ms,
            bid_tick: bid,
            ask_tick: ask,
        });
    }
}

/// Реплей всех суточных файлов символа сразу через несколько конфигураций
/// `H3` (таск 18, критерий приёмки «за один реплей на инструмент, не
/// пять»): декодирование бинлога, применение к книге — один проход по
/// файлам и кадрам, тем же кодом, что файловый `verify` (`Reader` читает
/// кадры, `FileReplayer` группирует их в обновления, `Book` их применяет);
/// `LevelTracker` на каждую конфигурацию развешивает уровни и трейды
/// независимо, кормится одними и теми же кадрами (`feed_frames_multi`).
/// Разрыв последовательности (`Err` из `apply`) останавливает файл для всех
/// конфигураций разом, как в `verify`: дальше этот файл недоверен,
/// следующий идёт с чистого листа. Возвращает `ReplayStats` в том же
/// порядке, что `cfgs`.
pub(crate) fn replay_symbol_over_configs(
    root: &Path,
    symbol: &str,
    cfgs: &[LevelsConfig],
) -> anyhow::Result<Vec<ReplayStats>> {
    anyhow::ensure!(
        !cfgs.is_empty(),
        "replay_symbol_over_configs: пустая сетка конфигураций"
    );
    let prefix = format!("{symbol}-");
    let files = list_symbol_binlogs(root, symbol)?;
    if files.is_empty() {
        // Таск 19, критерий 4: каталог старого формата (до таска 19 `lob
        // session` писала `<SYMBOL>.binlog` без даты) не должен молча
        // выглядеть как «нет суточных файлов» — владелец переименовывает
        // руками, но узнать об этом обязан из сообщения, не из тишины.
        let undated = root.join(format!("{symbol}.binlog"));
        if undated.is_file() {
            anyhow::bail!(
                "файл `{symbol}.binlog` без даты — запись старого формата, переименуйте в \
                 `{symbol}-<дата>.binlog` ({} в {})",
                undated.display(),
                root.display()
            );
        }
        anyhow::bail!("нет суточных файлов {prefix}*.binlog в {}", root.display());
    }
    // Счётчики GC (байт/записей) — по одному экземпляру на конфигурацию,
    // хотя декодирование общее: значения совпадут у всех, но `+=` читает
    // поле, а не только пишет его (та же идиома, что у `replay_symbol` до
    // этого таска — единственная актуальная альтернатива читать поле
    // разом после цикла тем же способом, каким это уже делает `ReplayStats
    // .days` ниже).
    let mut out: Vec<ReplayStats> = cfgs
        .iter()
        .map(|_| ReplayStats {
            days: Vec::new(),
            bytes: 0,
            records: 0,
            tick_e9: 0,
            step_e9: 0,
            open: Vec::new(),
        })
        .collect();
    let mut work: Vec<DayWork> = Vec::new();
    for path in &files {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day = day_of_filename(&prefix, &name)
            .ok_or_else(|| anyhow::anyhow!("имя {name} не разбирается как сутки"))?;
        let data = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
        for s in &mut out {
            s.bytes += data.len() as u64;
        }
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let header = reader.header();
        for s in &mut out {
            s.tick_e9 = header.tick_e9;
            s.step_e9 = header.step_e9;
        }
        if work.last().is_none_or(|w| w.day != day) {
            work.push(DayWork {
                day,
                records: cfgs.iter().map(|_| Vec::new()).collect(),
                touches: cfgs.iter().map(|_| Vec::new()).collect(),
                mids: Vec::new(),
                trackers: cfgs.iter().map(|&cfg| LevelTracker::new(cfg)).collect(),
            });
        }
        let entry = work
            .last_mut()
            .ok_or_else(|| anyhow::anyhow!("рабочий день только что добавлен, а его нет"))?;
        let mut book = Book::new(header.tick_e9, header.step_e9);
        let mut replayer = FileReplayer::new();
        let mut ups = Vec::new();
        let mut tps = Vec::new();
        let mut file_ok = true;
        loop {
            let frame = reader
                .read_frame()
                .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
            let Some(frame_records) = frame else { break };
            for s in &mut out {
                s.records += frame_records.len() as u64;
            }
            for rec in &frame_records {
                ups.clear();
                tps.clear();
                replayer.push_frame(
                    std::slice::from_ref(rec),
                    header.tick_e9,
                    header.step_e9,
                    &mut ups,
                    &mut tps,
                );
                let hit = trade_hit_from_record(rec);
                debug_assert_eq!(
                    tps.len(),
                    usize::from(hit.is_some()),
                    "разметка сделок обязана совпадать с FileReplayer"
                );
                for up in &ups {
                    if book.apply(up).is_err() {
                        file_ok = false;
                        break;
                    }
                    feed_frames_multi(
                        &book,
                        &mut entry.trackers,
                        up.cts_ms,
                        &mut entry.records,
                        &mut entry.touches,
                        &mut entry.mids,
                    );
                }
                if !file_ok {
                    break;
                }
                if let Some(h) = hit {
                    for tracker in &mut entry.trackers {
                        tracker.observe_trade(h);
                    }
                }
            }
            if !file_ok {
                break;
            }
        }
        if file_ok {
            let mut tail = Vec::new();
            replayer.finish(&mut tail);
            for up in &tail {
                if book.apply(up).is_err() {
                    break;
                }
                feed_frames_multi(
                    &book,
                    &mut entry.trackers,
                    up.cts_ms,
                    &mut entry.records,
                    &mut entry.touches,
                    &mut entry.mids,
                );
            }
        }
    }
    // Раскладка по конфигурации (таск 18): `w.records[i]` — уровни i-й
    // конфигурации за эти сутки, `w.mids` общий и клонируется в каждую
    // раскладку (срез середины не зависит от `H3`, дороже перечитать бинлог
    // ради него ещё раз, чем скопировать уже посчитанный вектор).
    let last = work.len().checked_sub(1);
    for (wi, w) in work.into_iter().enumerate() {
        // Живые уровни — только у последних суток: у прежних трекер
        // доигран до конца файла, и то, что там «живо», — обрыв записи.
        if Some(wi) == last {
            for (tracker, s) in w.trackers.iter().zip(out.iter_mut()) {
                tracker.live_levels(&mut s.open);
            }
        }
        for (i, (records, touches)) in w.records.into_iter().zip(w.touches).enumerate() {
            out[i].days.push(ReplayDay {
                day: w.day.clone(),
                records,
                touches,
                mids: w.mids.clone(),
            });
        }
    }
    Ok(out)
}

/// Реплей всех суточных файлов символа одной конфигурацией `H3` — частный
/// случай `replay_symbol_over_configs` с сеткой из одного элемента
/// (таск 18: код декодирования один, конфигураций может быть несколько).
pub(crate) fn replay_symbol(
    root: &Path,
    symbol: &str,
    cfg: LevelsConfig,
) -> anyhow::Result<ReplayStats> {
    let mut out = replay_symbol_over_configs(root, symbol, std::slice::from_ref(&cfg))?;
    Ok(out
        .pop()
        .expect("replay_symbol_over_configs с одним cfg обязан вернуть один ReplayStats"))
}

// ---------------------------------------------------------------------------
// Счётчики суток для `watch` и подтверждающего `markout`.
// ---------------------------------------------------------------------------

/// Собирает `DayTally` из реплея тем же `tally_day`, что читает G1.
/// Качество суток — из `verify.csv` шага 0.8 (расхождения теста 1) в корне
/// записи. Отсутствующий файл — ноль строк, а не ошибка: сутки без проверок
/// негодны по правилу `day_eligible` (ноль проверок — не годно), и это
/// честный красный, а не падение команды.
///
/// Знаменатель доли — строки с решённым сравнением (`Ok`/`Mismatch`),
/// числитель — строки `Mismatch`; `Misaligned`/`RestUnavailable` —
/// неопределённые, как `trades_indeterminate` в `verify`.
pub(crate) fn day_tallies(
    root: &Path,
    symbol: &str,
    days: &[ReplayDay],
) -> anyhow::Result<Vec<DayTally>> {
    let verify_rows =
        read_verify_rows(&verify_csv_path(root)).map_err(|e| anyhow::anyhow!("verify.csv: {e}"))?;
    days.iter()
        .map(|day| {
            let mut basis = 0u64;
            let mut violations = 0u64;
            for row in verify_rows
                .iter()
                .filter(|r| r.symbol == symbol && r.ts_utc.starts_with(&day.day))
            {
                match row.verdict {
                    VerifyVerdict::Ok => basis += 1,
                    VerifyVerdict::Mismatch => {
                        basis += 1;
                        violations += 1;
                    }
                    VerifyVerdict::Misaligned | VerifyVerdict::RestUnavailable => {}
                }
            }
            Ok(tally_day(symbol, &day.day, violations, basis))
        })
        .collect()
}
