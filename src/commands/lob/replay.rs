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
    ApproachRecord, LevelObs, LevelRecord, LevelTracker, LevelsConfig, LiveLevel, TouchRecord,
    TradeHit,
};
use crate::lob::markout::MidSample;
use crate::lob::sigma::thin_mids_tail_to_second_boundaries;
use crate::lob::watch::{tally_day, DayTally};
use hftbacktest::types::LOCAL_BUY_TRADE_EVENT;

use super::parts::{day_of_filename, list_symbol_binlogs};

// ---------------------------------------------------------------------------
// Общий реплей: суточные файлы символа → записи уровней и срезы середины.
// ---------------------------------------------------------------------------

/// Одни сутки UTC после реплея: записи уровней, касания живых уровней
/// (таск 35), записи подхода (F1 этапа F — пусто, если `cfg.approach_bps`
/// не задан) и срезы середины.
pub(crate) struct ReplayDay {
    pub(crate) day: String,
    pub(crate) records: Vec<LevelRecord>,
    pub(crate) touches: Vec<TouchRecord>,
    pub(crate) approaches: Vec<ApproachRecord>,
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
    approaches: Vec<Vec<ApproachRecord>>,
    mids: Vec<MidSample>,
    trackers: Vec<LevelTracker>,
}

/// Что реплей **оставляет** в памяти на сутки. Полный реплей держит все
/// записи уровней и срез середины по кадрам — это нужно `levels`/`markout`/
/// `dashboard`, но не сетке форм: ей нужны только касания, а записи и
/// середина за четверо суток монеты пула — больше гигабайта, и сетка на
/// сервере умирала по OOM (2026-09-18, `alpha-grid-20260917`). Трекер при
/// этом кормится теми же кадрами: касания, возраст уровней и прогрев не
/// зависят от того, хранится ли выход.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ReplayKeep {
    pub(crate) records: bool,
    pub(crate) mids: MidsKeep,
    /// Переносить возраст живых уровней через полночь (`--carry-age`, аудит дизайна 22.09 Т3):
    /// трекер следующих **смежных** суток наследует рождения уровней, стоящих на той же цене в
    /// первом кадре стороны. `false` — прежнее поведение (трекер с нуля в 00:00 UTC).
    pub(crate) carry_age: bool,
}

/// Что хранить из срезов середины.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum MidsKeep {
    /// Все срезы (markout, `touches`, дашборд).
    All,
    /// Только срезы «на границу секунды» — те, что `sample_asof` вернул бы
    /// на метку `s × 1000` мс (В-62: ряд `σ` в `lob::sigma` смотрит только
    /// на них; остальные — память: миллионы срезов на сутки символа).
    PerSecond,
}

impl ReplayKeep {
    pub(crate) const ALL: Self = Self {
        records: true,
        mids: MidsKeep::All,
        carry_age: false,
    };
    pub(crate) const TOUCHES_AND_SECOND_MIDS: Self = Self {
        records: false,
        mids: MidsKeep::PerSecond,
        carry_age: false,
    };

    /// То же хранение с переносом возраста через полночь.
    pub(crate) const fn with_carry_age(self, carry_age: bool) -> Self {
        Self { carry_age, ..self }
    }
}

/// `next` — следующие календарные сутки после `prev` (`YYYY-MM-DD`): перенос возраста только
/// через **смежную** полночь — пропущенные сутки означают, что уровень никто не видел.
pub(crate) fn is_next_day(prev: &str, next: &str) -> bool {
    let parse = |d: &str| chrono::NaiveDate::parse_from_str(d, "%Y-%m-%d").ok();
    match (parse(prev), parse(next)) {
        (Some(a), Some(b)) => a.succ_opt() == Some(b),
        _ => false,
    }
}

impl DayWork {
    /// Сбросить то, что не просили хранить, — после каждого кадра, чтобы
    /// векторы не росли (ёмкость остаётся на размер одного кадра).
    fn trim(&mut self, keep: ReplayKeep) {
        if !keep.records {
            for v in &mut self.records {
                v.clear();
            }
        }
        match keep.mids {
            MidsKeep::All => {}
            MidsKeep::PerSecond => thin_mids_tail_to_second_boundaries(&mut self.mids),
        }
    }
}

/// Трейд записи в трейд трекера. Отображение повторяет контракт писателя
/// (`record.rs::stage_trade`: `ev` из стороны агрессора, `block` — блочная)
/// и читателя (`verify.rs`: блочность из `block`); своей трактовки битов
/// здесь нет.
pub(crate) fn trade_hit_from_record(rec: &Record) -> Option<TradeHit> {
    if !is_trade_ev(rec.ev) {
        return None;
    }
    Some(TradeHit {
        tick: rec.price_ticks,
        lots: rec.qty_lots,
        aggressor_is_buy: rec.ev == LOCAL_BUY_TRADE_EVENT,
        block: rec.block,
        rpi: rec.rpi,
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
/// `touches`, записи подхода (F1) — в `approaches`, по вектору на
/// конфигурацию, как `out`. Вызывается только после успешного `apply`.
fn feed_frames_multi(
    book: &Book,
    trackers: &mut [LevelTracker],
    ts_ms: i64,
    out: &mut [Vec<LevelRecord>],
    touches: &mut [Vec<TouchRecord>],
    approaches: &mut [Vec<ApproachRecord>],
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
    debug_assert_eq!(
        trackers.len(),
        approaches.len(),
        "трекер и выход подходов обязаны идти парой"
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
        for (((tracker, out), touches), approaches) in trackers
            .iter_mut()
            .zip(out.iter_mut())
            .zip(touches.iter_mut())
            .zip(approaches.iter_mut())
        {
            tracker.observe_frame_with_approaches(ts_ms, side, &obs, out, touches, approaches);
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
    replay_symbol_over_configs_keep(root, symbol, cfgs, ReplayKeep::ALL)
}

/// То же, но с выбором, что хранить (`ReplayKeep`); `replay_symbol_over_configs`
/// — частный случай `ALL`.
pub(crate) fn replay_symbol_over_configs_keep(
    root: &Path,
    symbol: &str,
    cfgs: &[LevelsConfig],
    keep: ReplayKeep,
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
        // Архивный суффикс (T46) проверяется так же, как обычный.
        for suffix in [
            crate::binlog::BINLOG_SUFFIX,
            crate::binlog::BINLOG_ARCHIVE_SUFFIX,
        ] {
            let name = format!("{symbol}{suffix}");
            let undated = root.join(&name);
            if undated.is_file() {
                anyhow::bail!(
                    "файл `{name}` без даты — запись старого формата, переименуйте в \
                     `{symbol}-<дата>{suffix}` ({} в {})",
                    undated.display(),
                    root.display()
                );
            }
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
            let trackers: Vec<LevelTracker> = match work.last() {
                Some(prev) if keep.carry_age && is_next_day(&prev.day, &day) => prev
                    .trackers
                    .iter()
                    .zip(cfgs)
                    .map(|(t, &cfg)| LevelTracker::with_carried_births(cfg, t.live_births()))
                    .collect(),
                _ => cfgs.iter().map(|&cfg| LevelTracker::new(cfg)).collect(),
            };
            work.push(DayWork {
                day,
                records: cfgs.iter().map(|_| Vec::new()).collect(),
                touches: cfgs.iter().map(|_| Vec::new()).collect(),
                approaches: cfgs.iter().map(|_| Vec::new()).collect(),
                mids: Vec::new(),
                trackers,
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
            let frame = super::parts::read_frame_soft(&mut reader, path)?;
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
                        &mut entry.approaches,
                        &mut entry.mids,
                    );
                    entry.trim(keep);
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
                    &mut entry.approaches,
                    &mut entry.mids,
                );
                entry.trim(keep);
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
        for (i, ((records, touches), approaches)) in w
            .records
            .into_iter()
            .zip(w.touches)
            .zip(w.approaches)
            .enumerate()
        {
            out[i].days.push(ReplayDay {
                day: w.day.clone(),
                records,
                touches,
                approaches,
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

/// Реплей символа с касаниями и срезами середины **по границам секунд**
/// (`ReplayKeep::TOUCHES_AND_SECOND_MIDS`): столько, сколько нужно ряду `σ`
/// (В-62, `lob::sigma`), без записей уровней и без всех срезов.
pub(crate) fn replay_symbol_touches_and_second_mids(
    root: &Path,
    symbol: &str,
    cfg: LevelsConfig,
    carry_age: bool,
) -> anyhow::Result<ReplayStats> {
    let mut out = replay_symbol_over_configs_keep(
        root,
        symbol,
        std::slice::from_ref(&cfg),
        ReplayKeep::TOUCHES_AND_SECOND_MIDS.with_carry_age(carry_age),
    )?;
    Ok(out
        .pop()
        .expect("replay_symbol_over_configs_keep с одним cfg обязан вернуть один ReplayStats"))
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
