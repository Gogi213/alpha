//! Части бинлога сессии (В-34/T45): каталог потока, открытие файла части
//! инструмента и ротация по суткам UTC. Вынесено из `session.rs` (ревью
//! 23.09, W6): `impl SessionCtx` этого файла целиком про то, **где** и
//! **как** лежат части `<SYMBOL>-<день>[-pN].binlog` каждого потока глубины
//! — отдельно от того, что решает читать/писать пул (`pool_watch.rs`) и
//! ядра цикла событий (`session.rs`). Поведение не меняется — только
//! расположение кода: `session::tests` те же, что до разрезки.

use std::path::{Path, PathBuf};

use crate::binlog::Writer;
use crate::bybit::conn::{FAST_STREAM, SUBSCRIBED_DEPTHS};
use crate::commands::record::{
    claim_part_with, day_file_path, day_index_of_day_str, day_string_of_ns, ts_utc_of_ns, GapKind,
    FRAME_LOSS_WINDOW_SECS, FRAME_TARGET_RECORDS, NS_PER_DAY,
};
use crate::feed::live::PoolMember;

use super::sink::{
    flush_symbol_batch, push_book_snapshot, FrameSink, StreamState, SymbolState, STREAM_COUNT,
};
use super::summary::BinlogPart;
use super::{SessionCtx, DEEP_DIR};

/// Каталог файлов потока: быстрый `.50` — корень сессии (ровно как до T45),
/// глубокий `.200` — подкаталог `DEEP_DIR`. Единственное место, где эти два
/// каталога сопоставлены слотам `SUBSCRIBED_DEPTHS`.
pub(super) fn stream_dir(root: &Path, stream: usize) -> PathBuf {
    if stream == FAST_STREAM {
        root.to_path_buf()
    } else {
        root.join(DEEP_DIR)
    }
}

/// Открывает файл части символа под каталогом потока на сутки `day`:
/// следующий свободный номер через `record::claim_part_with` (таск 25 — один
/// цикл поиска на `lob record` и `lob session`, приёмник — `FrameSink`),
/// ничего не затирает — вторая сессия тех же суток получает `-p2`, не
/// перезаписывает первую (таск 22, критерий 2). Части потоков нумеруются
/// **независимо**: у быстрого и глубокого свои каталоги, и `-p2` одного не
/// значит `-p2` другого.
pub(super) fn claim_symbol_binlog(
    dir: &Path,
    symbol: &str,
    day: &str,
    tick_e9: i64,
    step_e9: i64,
) -> anyhow::Result<(Writer<FrameSink>, u32)> {
    claim_part_with(dir, symbol, day, 1, tick_e9, step_e9, FrameSink::new)
        .map_err(|e| anyhow::anyhow!("{symbol}: {e}"))
}

/// Состояние инструмента на старте/добавлении на ходу (таск 34): по файлу
/// части и по книге на **каждый** поток глубины (`SUBSCRIBED_DEPTHS`, T45).
/// Первый слот — быстрый, его файл лежит в корне, как раньше; остальные — в
/// своём подкаталоге (`stream_dir`). Отказ на любом потоке — отказ всего
/// инструмента: часть без соседней части не запись, поэтому уже открытые
/// файлы этой попытки убираются (иначе повтор получил бы `-p2` вместо
/// свободной части 1) — и убираются **после** закрытия писателей
/// (`cleanup_failed_open`): удалять открытый файл — это на Windows удаление
/// пометкой, а не удаление.
pub(super) fn open_symbol_state(
    root: &Path,
    member: &PoolMember,
    day: &str,
) -> anyhow::Result<SymbolState> {
    let day_index =
        day_index_of_day_str(day).map_err(|e| anyhow::anyhow!("{}: {e}", member.symbol))?;
    let mut opened: Vec<(PathBuf, u32)> = Vec::with_capacity(STREAM_COUNT);
    let mut streams: Vec<StreamState> = Vec::with_capacity(STREAM_COUNT);
    for (slot, &depth) in SUBSCRIBED_DEPTHS.iter().enumerate() {
        let dir = stream_dir(root, slot);
        let claimed = std::fs::create_dir_all(&dir)
            .map_err(|e| anyhow::anyhow!("{}: каталог {}: {e}", member.symbol, dir.display()))
            .and_then(|()| {
                claim_symbol_binlog(&dir, &member.symbol, day, member.tick_e9, member.step_e9)
            });
        match claimed {
            Ok((writer, part)) => {
                opened.push((dir, part));
                streams.push(StreamState::new(depth, member, writer, part, day_index));
            }
            Err(e) => {
                return Err(cleanup_failed_open(&member.symbol, day, opened, streams, e));
            }
        }
    }
    let streams = streams
        .try_into()
        .map_err(|_| anyhow::anyhow!("{}: потоков не {STREAM_COUNT}", member.symbol))?;
    Ok(SymbolState {
        member: member.clone(),
        streams,
        active: true,
    })
}

/// Уборка после несостоявшегося открытия инструмента: писатели уже открытых
/// потоков закрываются (`drop`) **до** удаления их файлов, и только потом
/// файлы уходят с диска. Порядок — не косметика: `std::fs::remove_file` по
/// открытому файлу на Windows снимает имя, а данные живут до закрытия
/// последней ссылки, то есть «удалённый» файл мог бы достаться следующей
/// попытке (`claim_part_with` увидел бы имя свободным, а место — занятым).
fn cleanup_failed_open(
    symbol: &str,
    day: &str,
    opened: Vec<(PathBuf, u32)>,
    streams: Vec<StreamState>,
    err: anyhow::Error,
) -> anyhow::Error {
    drop(streams);
    for (dir, part) in opened {
        let _ = std::fs::remove_file(day_file_path(&dir, symbol, day, part));
    }
    err
}

impl SessionCtx {
    /// Ротация по суткам UTC (таск 25, как `record::Recorder::ensure_day`):
    /// сутки события биржи **позже** суток файла инструмента — недописанный
    /// батч кадром в старые сутки, новая часть новых суток
    /// (`open_next_part`). Только вперёд: опоздавшее событие прошлых суток
    /// (сделка с `T` раньше `cts` уже принятой дельты) идёт в текущую
    /// часть — иначе части D/D+1 чередовались бы `-p2/-p3` на каждом таком
    /// событии. Отказ ротации — строка stderr и `gaps.csv`, события идут в
    /// текущую часть, повтор не раньше `FRAME_LOSS_WINDOW_SECS`; цикл не
    /// останавливается. Полночь — не разрыв, строки в `gaps.csv` нет.
    ///
    /// Ротация — **по потоку события** (T45): `.200` и `.50` живут своими
    /// частями, и сутки одного не тянут за собой файл другого. Сутки
    /// сравниваются с индексом суток файла своего потока.
    pub(super) fn rotate_symbol_day(
        &mut self,
        idx: usize,
        stream: usize,
        exch_ts_ns: i64,
        local_ts_ns: i64,
    ) {
        let day_index = exch_ts_ns.div_euclid(NS_PER_DAY);
        let Some(state) = self.states.get(idx) else {
            return;
        };
        let depth = state.streams[stream].depth;
        if day_index <= state.streams[stream].day_index
            || local_ts_ns < state.streams[stream].rotate_retry_after_ns
        {
            return;
        }
        self.flush_symbol(idx, stream, local_ts_ns);
        if let Err(e) = self.open_next_part(idx, stream, day_index, exch_ts_ns, local_ts_ns) {
            let stream_state = &mut self.states[idx].streams[stream];
            stream_state.rotate_retry_after_ns =
                local_ts_ns.saturating_add(FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000);
            let detail = format!(
                "поток .{depth}: ротация суток не удалась: {e} — события идут в часть p{} \
                 прежних суток, повтор через {FRAME_LOSS_WINDOW_SECS} с",
                stream_state.part
            );
            eprintln!("session: {}: {detail}", self.states[idx].member.symbol);
            self.log_gap(
                Some(idx),
                GapKind::WriteFailed,
                ts_utc_of_ns(local_ts_ns),
                detail,
            );
        }
    }

    /// Следующая свободная часть суток `day_index` для потока инструмента
    /// (`claim_part_with`, ничего не затирается): общий шов ротации по
    /// полуночи и переоткрытия после потерянной границы кадра. Первым
    /// кадром — синтетический снапшот книги **этого потока**, если она
    /// доверена (`synced`); иначе файл ждёт снапшота биржи, как при старте.
    /// `started_ns` — `started_utc` части в `binlog_files`; `session.json`
    /// переписывается сразу (best-effort) — `binlog_files` читают
    /// `profiles`/`watch`. В `binlog_files` попадает **только быстрый**
    /// поток: этот список — карта основных файлов каталога (его читают
    /// `session_parts_for`/`profiles`/`watch` резолвером корня), и вторая
    /// запись с теми же `(symbol, part)` на глубокий файл сделала бы атрибуцию
    /// частей неоднозначной, ничего не добавив читателям.
    pub(super) fn open_next_part(
        &mut self,
        idx: usize,
        stream: usize,
        day_index: i64,
        started_ns: i64,
        local_ts_ns: i64,
    ) -> anyhow::Result<()> {
        let day = day_string_of_ns(day_index.saturating_mul(NS_PER_DAY))
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        let state = &mut self.states[idx];
        let dir = stream_dir(&self.root, stream);
        let (writer, part) = claim_symbol_binlog(
            &dir,
            &state.member.symbol,
            &day,
            state.member.tick_e9,
            state.member.step_e9,
        )?;
        // Старый приёмник закрывается вместе с прежним `Writer` — его буфер
        // уже пуст после сброса у вызывающего.
        let stream_state = &mut state.streams[stream];
        stream_state.writer = writer;
        stream_state.part = part;
        stream_state.day_index = day_index;
        stream_state.has_snapshot = false;
        stream_state.batch.clear();
        if stream == FAST_STREAM {
            self.binlog_files.push(BinlogPart {
                symbol: state.member.symbol.clone(),
                part,
                started_utc: ts_utc_of_ns(started_ns),
            });
        }
        if stream_state.synced {
            push_book_snapshot(stream_state, started_ns, local_ts_ns);
            // Не `flush_symbol`: та на потерянной границе переоткрыла бы
            // часть снова — рекурсия на мёртвом диске.
            if let Err(e) = flush_symbol_batch(stream_state) {
                let detail =
                    format!("кадр не записался ({e}) — потеряно до {FRAME_TARGET_RECORDS} записей");
                self.log_gap(
                    Some(idx),
                    GapKind::WriteFailed,
                    ts_utc_of_ns(local_ts_ns),
                    detail,
                );
            }
        }
        // Не пишем `session.json` здесь (таск 28): на 761 инструменте одна
        // полночь — 761 ротация, и запись на каждой означала бы 761
        // сериализацию списка из 761 части (99 КБ) — ≈ 75 МБ на диск за
        // секунду потоком решений, в котором стоит очередь событий. Ротация
        // только помечает; пишет первый тик после неё, не позже
        // `FRAME_LOSS_WINDOW_SECS`, один раз на всех.
        self.session_json_dirty = true;
        Ok(())
    }
}

// -----------------------------------------------------------------------
// Грепом по образцу `session.rs::hot_path_guard`/`sink.rs::hot_path_guard`
// (аудит 17.09/18.09: тест был только у `session.rs` целиком, разрезка не
// имеет права молча вынести код горячего пути мимо него).
// -----------------------------------------------------------------------
#[cfg(test)]
mod hot_path_guard {
    /// `HashMap`/`BTreeMap` — запрет 7 (лукап по символу — `u16`/индекс, не
    /// хеш); `SystemTime::now` — стенные часы мимо трейта `Clock`. Строки
    /// собраны из частей, иначе литерал триггерил бы проверку сам на себя.
    #[test]
    fn parts_have_no_maps_and_no_wall_clock_outside_the_trait() {
        const SRC: &str = include_str!("parts.rs");
        let banned = [
            concat!("Hash", "Map"),
            concat!("BTree", "Map"),
            concat!("System", "Time::now"),
        ];
        for (n, line) in SRC.lines().enumerate() {
            if line.trim_start().starts_with("//") {
                continue; // объяснения, почему этих слов тут нет, — не код
            }
            for b in banned {
                assert!(
                    !line.contains(b),
                    "строка {} тянет запрещённое ({b}): {line}",
                    n + 1
                );
            }
        }
    }
}
