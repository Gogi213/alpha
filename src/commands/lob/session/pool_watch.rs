//! Слежение за `<root>/instruments.csv` на ходу (таск 34 — добавление, A8.1 —
//! снятие). Вынесено из `session.rs` (ревью 23.09, W6): `check_pool_file` и
//! его три копии одного отката (`eprintln!` + `pool_retry = true` +
//! `discard_opened` + `return`) собраны здесь одним местом отката —
//! `reload_pool`/`apply_removals`/`fresh_members`/`add_batch`. Поведение не
//! меняется — `session::tests` те же, что до разрезки.

use std::path::{Path, PathBuf};

use crate::bybit::conn::{Clock, DEEP_STREAM, FAST_STREAM, SUBSCRIBED_DEPTHS};
use crate::commands::record::{day_file_path, day_string_of_ns, ts_utc_of_ns, GapKind};
use crate::feed::live::PoolMember;
use crate::feed::DynamicPool;

use super::parts::{open_symbol_state, stream_dir};
use super::pool::load_pool;
use super::summary::BinlogPart;
use super::SessionCtx;

/// `mtime` файла или `None`, если файла нет / метаданные не читаются —
/// оба случая для слежения равнозначны «сравнивать не с чем».
pub(super) fn file_mtime(path: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// Файл пула дописан целиком (A8.1): последний байт — перевод строки.
/// `load_pool` уже отвергает пустой и неразобранный файл (оборванная строка —
/// ошибка разбора), но перезапись «в тот же файл» оставляет окно, в котором
/// строки кончаются ровно на границе: такой файл от целого неотличим ничем,
/// кроме последнего байта. Отсюда регламент (`COMMANDS.md`): пул пишется
/// **атомарно** (временный файл + переименование) или дописывается строками
/// (`>>`), а не переписывается по месту. Чего это правило **не** ловит:
/// файл, обрезанный ровно по границе строки (например, оборванный `head`) —
/// он выглядит дописанным, и его состав применяется как есть; надёжнее метки
/// поколения пула ничего нет, а она — отдельное решение владельца, не
/// изобретённое здесь.
pub(super) fn pool_file_is_complete(path: &Path) -> bool {
    std::fs::read(path)
        .map(|bytes| bytes.last() == Some(&b'\n'))
        .unwrap_or(false)
}

/// Партия не состоялась (таск 34): закрыть уже открытые части и убрать их
/// файлы, чтобы в каталоге не остались пустые заголовки, а повтор не получил
/// `-p2`. Файлов теперь два на инструмент — по одному на поток; пути
/// собираются **до** `drop`, а удаление идёт после закрытия писателей (тот же
/// порядок и та же причина, что в `cleanup_failed_open`). Сам подкаталог
/// `deep/` не удаляется: пустой каталог никому не мешает, а его создание —
/// работа следующей попытки.
pub(super) fn discard_opened(root: &Path, day: &str, opened: Vec<super::sink::SymbolState>) {
    for state in opened {
        let paths: Vec<PathBuf> = state
            .streams
            .iter()
            .enumerate()
            .map(|(slot, stream)| {
                day_file_path(
                    &stream_dir(root, slot),
                    &state.member.symbol,
                    day,
                    stream.part,
                )
            })
            .collect();
        drop(state);
        for path in paths {
            let _ = std::fs::remove_file(path);
        }
    }
}

impl SessionCtx {
    /// Слежение за `<root>/instruments.csv` (таск 34 — добавление, A8.1 —
    /// снятие): `mtime` не изменился — выход после одного `metadata()`.
    /// Изменился — файл перечитан целиком (`load_pool`, тот же разбор, что
    /// на старте) и **только если он целый**: разобрался без ошибок и
    /// кончается переводом строки (`pool_file_is_complete` — редактор пишет
    /// не атомарно, оборванный файл не должен читаться как «убрать монеты»).
    /// Дальше состав приводится к файлу **одной перечиткой**: активные
    /// символы, которых в файле больше нет, снимаются с записи
    /// (`close_symbol` + `feed.remove`), новые (в том числе вернувшиеся имена
    /// — они уже не активны, индекс не переиспользуется) получают файл части
    /// текущих суток UTC (`open_symbol_state`, следующая свободная часть) и
    /// уходит в источник одной партией (`feed.add`); индексы обязаны
    /// совпасть с позициями в `states` — иначе кадр ушёл бы в чужой файл.
    /// Замена монеты — это те же два шага за один `mtime`, а полная замена
    /// пула (ни одного общего имени) — снятие всех плюс добавление новой
    /// партии. Ошибка чтения файла, строки или источника — строка stderr,
    /// запись идёт; **отказ на шаге добавления повторяется на следующем тике**
    /// (`pool_retry`, A8.1b: правка файла одноразовая — ждать нового `mtime`
    /// после уже применённого снятия значило бы потерять монету до следующей
    /// правки пула).
    pub(super) fn check_pool_file<F: DynamicPool + ?Sized, C: Clock>(
        &mut self,
        feed: &mut F,
        ts_ns: i64,
        clock: &C,
    ) {
        let Some(pool) = self.reload_pool() else {
            return;
        };
        let Some(removed_any) = self.apply_removals(feed, &pool, ts_ns) else {
            // Снятие не состоялось — источник отказал (строка stderr уже
            // напечатана в `apply_removals`): состав прежний, повтор при
            // следующем изменении файла.
            return;
        };
        let fresh = fresh_members(&self.states, pool);
        if fresh.is_empty() {
            self.pool_retry = false;
            if !removed_any {
                return;
            }
            // Снятие без добавления: в файлах и `states` уже ничего не
            // меняется, а `session.json` обязан показать снятие.
            self.session_json_dirty = false;
            self.write_session_json_or_log(ts_ns, clock);
            return;
        }
        self.add_batch(feed, fresh, ts_ns, clock);
    }

    /// Шаг «перечитать» из doc `check_pool_file`: `mtime` не изменился и
    /// повтора не просили — `None` без единого чтения файла целиком;
    /// изменился — `load_pool`, но только если файл дописан целиком
    /// (`pool_file_is_complete`). Каждый ранний выход печатает причину и
    /// снимает `pool_retry` сам — состав приводить дальше нечего.
    fn reload_pool(&mut self) -> Option<Vec<PoolMember>> {
        let mtime = file_mtime(&self.pool_path);
        if mtime == self.pool_mtime && !self.pool_retry {
            return None;
        }
        self.pool_mtime = mtime;
        if mtime.is_none() {
            self.pool_retry = false;
            return None;
        }
        if !pool_file_is_complete(&self.pool_path) {
            eprintln!(
                "session: {} изменён, но не дописан (нет перевода строки в конце) — \
                 состав записи прежний, повтор при следующем изменении файла; пул пиши \
                 атомарно (временный файл + переименование) или дописывай строки",
                self.pool_path.display()
            );
            self.pool_retry = false;
            return None;
        }
        match load_pool(&self.pool_path) {
            Ok(pool) => Some(pool),
            Err(e) => {
                eprintln!(
                    "session: {} изменился, но не прочитан: {e} — состав записи прежний, \
                     повтор при следующем изменении файла",
                    self.pool_path.display()
                );
                self.pool_retry = false;
                None
            }
        }
    }

    /// Снятие (A8.1) — до добавления: замена монеты это «убрать» и
    /// «добавить» за одну перечитку, а снятый индекс (и имя) не должен
    /// мешать возврату того же символа в пул (он получит новый индекс).
    /// `None` — источник отказал снять партию, состав приводить дальше не
    /// нужно (вызывающий уходит без добавления); `Some(true/false)` — было
    /// ли что снимать.
    fn apply_removals<F: DynamicPool + ?Sized>(
        &mut self,
        feed: &mut F,
        pool: &[PoolMember],
        ts_ns: i64,
    ) -> Option<bool> {
        let removed: Vec<usize> = self
            .states
            .iter()
            .enumerate()
            .filter(|(_, s)| s.active && !pool.iter().any(|m| m.symbol == s.member.symbol))
            .map(|(idx, _)| idx)
            .collect();
        if removed.is_empty() {
            return Some(false);
        }
        let indices: Vec<u16> = removed
            .iter()
            .map(|&idx| u16::try_from(idx).unwrap_or(u16::MAX))
            .collect();
        match feed.remove(&indices) {
            Ok(seams) => {
                // A8.1b: соседи по снятому сокету переехали в новый — у
                // них между остановкой старого соединения и снапшотом
                // нового данных нет. Шов покрытия: строка `gaps.csv` на
                // каждого (В-59 сутки не роняет) и сброс доверия книги —
                // до снапшота дельты нового сокета не на ту книгу.
                let names = removed
                    .iter()
                    .filter_map(|&idx| self.states.get(idx))
                    .map(|s| s.member.symbol.as_str())
                    .collect::<Vec<_>>()
                    .join(", ");
                for &idx in &seams {
                    let idx = usize::from(idx);
                    if let Some(state) = self.states.get_mut(idx) {
                        for stream in &mut state.streams {
                            stream.synced = false;
                        }
                    }
                    self.gaps += 1;
                    self.log_gap(
                        Some(idx),
                        GapKind::SequenceGap,
                        ts_utc_of_ns(ts_ns),
                        format!(
                            "шов покрытия — пересборка сокета при снятии {names}: \
                             снапшот нового соединения ещё не пришёл"
                        ),
                    );
                }
            }
            Err(e) => {
                eprintln!(
                    "session: снятие не состоялось — источник отказал: {e}; состав записи \
                     прежний, повтор при следующем изменении {}",
                    self.pool_path.display()
                );
                return None;
            }
        }
        for &idx in &removed {
            self.close_symbol(idx, ts_ns);
        }
        Some(true)
    }

    /// Добавление новой партии (таск 34): открывает файлы частей всех
    /// `fresh`, зовёт источник одной партией и приводит `states`/
    /// `binlog_files` к результату. Отказ на любом шаге — `abort_add`: одно
    /// место отката вместо трёх копий «`eprintln!` + `pool_retry = true` +
    /// `discard_opened` + `return`» (ревью 23.09, W6).
    fn add_batch<F: DynamicPool + ?Sized, C: Clock>(
        &mut self,
        feed: &mut F,
        fresh: Vec<PoolMember>,
        ts_ns: i64,
        clock: &C,
    ) {
        let day = match day_string_of_ns(ts_ns) {
            Ok(day) => day,
            Err(e) => {
                eprintln!("session: добавление отложено — сутки не вычислены: {e}");
                return;
            }
        };
        let mut opened: Vec<super::sink::SymbolState> = Vec::with_capacity(fresh.len());
        for member in &fresh {
            match open_symbol_state(&self.root, member, &day) {
                Ok(state) => opened.push(state),
                Err(e) => {
                    return self.abort_add(
                        &day,
                        opened,
                        format!(
                            "session: {} не добавлен — файл части не открыт: {e}; \
                             повтор на следующем тике (файл пула менять не нужно)",
                            member.symbol
                        ),
                    );
                }
            }
        }
        let indices = match feed.add(fresh) {
            Ok(indices) => indices,
            Err(e) => {
                return self.abort_add(
                    &day,
                    opened,
                    format!(
                        "session: партия не добавлена — источник отказал: {e}; файлы частей \
                         удалены, повтор на следующем тике (файл пула менять не нужно)"
                    ),
                );
            }
        };
        // Индекс нового инструмента обязан совпасть с его позицией в `states`
        // — иначе кадр ушёл бы в чужой файл. `LiveFeed` это гарантирует по
        // построению; чужая реализация `DynamicPool` может нарушить, и
        // суточный коллектор на это не паникует: партия отбрасывается, а
        // события этих индексов цикл пропускает (`idx >= states.len()`).
        let expected: Vec<u16> = (0..opened.len())
            .map(|i| u16::try_from(self.states.len() + i).unwrap_or(u16::MAX))
            .collect();
        if indices != expected {
            return self.abort_add(
                &day,
                opened,
                format!(
                    "session: партия не добавлена — источник вернул индексы {indices:?}, \
                     ожидались {expected:?}; файлы частей удалены, повтор на следующем тике"
                ),
            );
        }
        let started_utc = ts_utc_of_ns(ts_ns);
        for state in opened {
            // Печатается и в `binlog_files` попадает быстрый поток — тот
            // файл, который читают все прежние команды (см. doc
            // `open_next_part`); глубокий — рядом, в подкаталоге, и о нём
            // строка stderr сообщает отдельно.
            let fast = &state.streams[FAST_STREAM];
            eprintln!(
                "session: добавлен {} (tick={}, step={}) — файл {}; глубокий поток .{} — {}",
                state.member.symbol,
                state.member.tick_e9,
                state.member.step_e9,
                day_file_path(&self.root, &state.member.symbol, &day, fast.part).display(),
                SUBSCRIBED_DEPTHS[DEEP_STREAM],
                day_file_path(
                    &stream_dir(&self.root, DEEP_STREAM),
                    &state.member.symbol,
                    &day,
                    state.streams[DEEP_STREAM].part,
                )
                .display()
            );
            self.binlog_files.push(BinlogPart {
                symbol: state.member.symbol.clone(),
                part: fast.part,
                started_utc: started_utc.clone(),
            });
            self.states.push(state);
        }
        self.session_json_dirty = false;
        self.pool_retry = false;
        self.write_session_json_or_log(ts_ns, clock);
    }

    /// Единственное место отката добавления (A8.1b): печатает причину,
    /// просит повтор на следующем тике без ожидания нового `mtime` и убирает
    /// уже открытые файлы частей этой попытки (`discard_opened`).
    fn abort_add(&mut self, day: &str, opened: Vec<super::sink::SymbolState>, message: String) {
        eprintln!("{message}");
        self.pool_retry = true;
        discard_opened(&self.root, day, opened);
    }
}

/// Новые символы файла пула — те, для кого нет **активного** состояния в
/// `states`: снятый символ, вернувшийся в файл, добавляется заново (новый
/// индекс, новая часть, своё соединение). `pool` — по значению: элементы
/// переезжают в `fresh` (или отбрасываются), без лишнего клона — как в
/// исходном цикле `check_pool_file` до разрезки.
fn fresh_members(states: &[super::sink::SymbolState], pool: Vec<PoolMember>) -> Vec<PoolMember> {
    let mut fresh: Vec<PoolMember> = Vec::new();
    for member in pool {
        let known = states
            .iter()
            .any(|s| s.active && s.member.symbol == member.symbol)
            || fresh.iter().any(|m| m.symbol == member.symbol);
        if !known {
            fresh.push(member);
        }
    }
    fresh
}

// -----------------------------------------------------------------------
// Грепом по образцу `session.rs::hot_path_guard`/`sink.rs::hot_path_guard`.
// -----------------------------------------------------------------------
#[cfg(test)]
mod hot_path_guard {
    #[test]
    fn pool_watch_has_no_maps_and_no_wall_clock_outside_the_trait() {
        const SRC: &str = include_str!("pool_watch.rs");
        let banned = [
            concat!("Hash", "Map"),
            concat!("BTree", "Map"),
            concat!("System", "Time::now"),
        ];
        for (n, line) in SRC.lines().enumerate() {
            if line.trim_start().starts_with("//") {
                continue;
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
