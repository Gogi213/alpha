//! `lob session` — сессия по всему пулу одновременно (таск 04, история 7)
//! и олвейс-он коллектор (таск 25, В-34: «повесить олвейсон коллектор, но
//! супер экономный»).
//!
//! Тонкая оболочка поверх `feed::live::LiveFeed`: сама сессия не решает,
//! откуда идут события — ядро `run_session_loop` держит `&mut dyn Feed` и
//! не отличило бы живой сокет от `feed::replay::ReplayFeed`, если бы
//! получило его вместо (это и есть критерий приёмки «вызывающий код не
//! различает», `interfaces.md`; тесты этого файла кормят ядро сценарным
//! `Feed`). Один поток решений читает `Feed::next_event()` в цикле, без
//! `async`, без `tokio` в этой функции — только вызов, который сам
//! блокируется на канале (`ARCHITECTURE.md` A3).
//!
//! Периодика (таск 25) — **по таймеру рантайма, не по приходу события**:
//! живой `Feed` шлёт `Event::Tick` раз в `record::FRAME_LOSS_WINDOW_SECS`
//! даже при полном молчании пула; на тике сбрасываются накопленные кадры
//! (окно потери при крахе — этот же период), раз в
//! `record::HOURLY_REFRESH_SECS` переписывается `session.json` и печатается
//! одна строка сводки. Остановка — `None` от `Feed` (Ctrl+C через
//! `feed::live::StopHandle`, тот же путь, что сигнал-заменитель в тестах).
//! Сутки UTC — новая часть `<SYMBOL>-<день>.binlog` через `record::
//! claim_part_with`, первым кадром — синтетический снапшот книги, как у
//! `lob record`.
//!
//! Пул на ходу (таск 34, R89): `<root>/instruments.csv` — живой файл. На
//! тике (не чаще `FRAME_LOSS_WINDOW_SECS`) один `metadata()`; изменился
//! `mtime` — файл перечитан тем же `load_pool`, символы, которых ещё нет в
//! `states`, получают файл части текущих суток и своё соединение
//! (`feed::DynamicPool::add`); индексы обязаны продолжить `states`.
//! Удаление строки ничего не останавливает — снятие не поддерживается.

mod args;
mod pool;
mod resources;
mod sink;
mod summary;

pub use args::{
    SessionArgs, SessionPlan, MAX_MINUTES, MAX_PILOT_MINUTES, MIN_MINUTES, MIN_PILOT_MINUTES,
};
pub(crate) use sink::FrameSink;
pub use summary::{BinlogPart, ResourceSample, SessionSummary};

use args::{is_debug_session, resolve_duration};
use pool::{load_pool, resolve_pool};
use resources::{
    hour_utc_of_ns, sample_resources, spawn_clock_sampler, spawn_resource_sampler,
    take_clock_sample,
};
use sink::{
    flush_symbol_batch, push_book_snapshot, write_market_event, LatencyHistogram, SymbolState,
    SESSION_MAX_FRAME_RECORDS,
};

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::binlog::Writer;
use crate::book::Book;
use crate::bybit::conn::{Clock, SystemClock};
use crate::commands::record::{
    append_gap_row, claim_part_with, day_file_path, day_string_of_ns, ensure_gaps_csv,
    event_exch_ms, gaps_csv_path, ts_utc_of_ns, GapKind, GapRow, FRAME_LOSS_WINDOW_SECS,
    FRAME_TARGET_RECORDS, HOURLY_REFRESH_SECS, NS_PER_DAY,
};
use crate::feed::live::{LiveFeed, PoolMember};
use crate::feed::{DynamicPool, Event, Feed, GapKind as FeedGapKind};

/// Открывает файл части символа под `root` на сутки `day`: следующий
/// свободный номер через `record::claim_part_with` (таск 25 — один цикл
/// поиска на `lob record` и `lob session`, приёмник — `FrameSink`), ничего
/// не затирает — вторая сессия тех же суток получает `-p2`, не
/// перезаписывает первую (таск 22, критерий 2).
fn claim_symbol_binlog(
    root: &Path,
    symbol: &str,
    day: &str,
    tick_e9: i64,
    step_e9: i64,
) -> anyhow::Result<(Writer<FrameSink>, u32)> {
    claim_part_with(root, symbol, day, 1, tick_e9, step_e9, FrameSink::new)
        .map_err(|e| anyhow::anyhow!("{symbol}: {e}"))
}

fn open_symbol_state(root: &Path, member: &PoolMember, day: &str) -> anyhow::Result<SymbolState> {
    let (writer, part) =
        claim_symbol_binlog(root, &member.symbol, day, member.tick_e9, member.step_e9)?;
    let day_index = crate::commands::record::day_index_of_day_str(day)
        .map_err(|e| anyhow::anyhow!("{}: {e}", member.symbol))?;
    Ok(SymbolState {
        member: member.clone(),
        writer,
        part,
        day_index,
        book: Book::new(member.tick_e9, member.step_e9),
        synced: false,
        has_snapshot: false,
        records_written: 0,
        rotate_retry_after_ns: i64::MIN,
        frames_failed: 0,
        // 50 бид + 50 аск — самый крупный кадр потока (`orderbook.50`
        // снапшот); запас, чтобы `.push` внутри `write_market_event`
        // не перевыделял на первом же снапшоте.
        scratch: Vec::with_capacity(128),
        // `FRAME_TARGET_RECORDS` плюс тот же запас на самое крупное
        // сообщение — `Vec::append` из `scratch` не перевыделяет, даже
        // если порог пересечён ровно этим сообщением (флаш случится
        // сразу после, но до него длина временно больше порога).
        batch: Vec::with_capacity(SESSION_MAX_FRAME_RECORDS),
    })
}

/// Партия не состоялась (таск 34): закрыть уже открытые части и убрать их
/// файлы, чтобы в каталоге не остались пустые заголовки, а повтор не получил
/// `-p2`.
fn discard_opened(root: &Path, day: &str, opened: Vec<SymbolState>) {
    for state in opened {
        let path = day_file_path(root, &state.member.symbol, day, state.part);
        drop(state);
        let _ = std::fs::remove_file(path);
    }
}

/// Читает `binlog_files` уже существующего `session.json` под `root`, если
/// он есть и разбирается — вторая сессия тех же суток дописывает свои части
/// к этой истории, не начинает список заново (doc `SessionSummary::
/// binlog_files`). Нет файла, не читается, старый формат без поля — пустой
/// список (`#[serde(default)]` уже прощает последнее), не ошибка: это
/// лучшее усилие по накоплению истории, а не критерий приёмки сам по себе.
fn read_previous_binlog_files(root: &Path) -> Vec<BinlogPart> {
    std::fs::read_to_string(root.join("session.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<SessionSummary>(&s).ok())
        .map(|old| old.binlog_files)
        .unwrap_or_default()
}

/// Всё состояние прогона между событиями — то, что ядру `run_session_loop`
/// нужно кроме самого `Feed`. Открывается без сети (`open`), поэтому тесты
/// собирают его на временном каталоге и кормят сценарным `Feed`.
struct SessionCtx {
    root: PathBuf,
    plan: SessionPlan,
    started_ns: i64,
    started_utc: String,
    start_hour_utc: u32,
    /// `None` у олвейс-он: конец — только `None` от `Feed`.
    deadline_ns: Option<i64>,
    states: Vec<SymbolState>,
    binlog_files: Vec<BinlogPart>,
    gaps_path: PathBuf,
    gaps: u64,
    reconnects: u64,
    resyncs: u64,
    unrouted: u64,
    /// Ротация суток изменила `binlog_files`, а `session.json` ещё не
    /// переписан — пишет первый тик после ротации, один раз на всех
    /// (таск 28, см. `on_tick`).
    session_json_dirty: bool,
    // Суббюджет «разбор» (`PLAN.md` 3.1, `p99 < 200 мкс`) и очередь
    // (таск 20) — гистограммы фиксированной ёмкости (таск 24), не `Vec`
    // всех замеров: RSS плоский на любой длине прогона.
    parse_latencies_ns: LatencyHistogram,
    queue_latencies_ns: LatencyHistogram,
    samples: Arc<Mutex<Vec<ResourceSample>>>,
    clock_samples: Arc<AtomicU64>,
    resources_pid: u32,
    resources_start: Option<(f64, u64)>,
    resources_wall_start: std::time::Instant,
    last_hourly_ns: i64,
    hours_reported: u64,
    /// `<root>/instruments.csv` и его `mtime` на последнем чтении (таск 34):
    /// файл перечитывается только когда метка изменилась — одна проверка
    /// метаданных на тик, ни одной на событие рынка.
    pool_path: PathBuf,
    pool_mtime: Option<std::time::SystemTime>,
}

/// `mtime` файла или `None`, если файла нет / метаданные не читаются —
/// оба случая для слежения равнозначны «сравнивать не с чем».
fn file_mtime(path: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

impl SessionCtx {
    fn open(
        root: &Path,
        pool: &[PoolMember],
        plan: SessionPlan,
        started_ns: i64,
    ) -> anyhow::Result<Self> {
        std::fs::create_dir_all(root)?;
        let started_utc = ts_utc_of_ns(started_ns);
        let start_hour_utc = hour_utc_of_ns(started_ns);
        // День решается один раз, до открытия файлов: `verify`/`levels`/
        // `markout` ищут `<SYMBOL>-<день>.binlog` (таск 19); дальше сутки
        // ведёт ротация по времени биржи (`rotate_symbol_day`).
        let day = day_string_of_ns(started_ns).map_err(|e| anyhow::anyhow!("{e}"))?;
        let mut binlog_files = read_previous_binlog_files(root);
        let mut states = Vec::with_capacity(pool.len());
        for member in pool {
            let state = open_symbol_state(root, member, &day)?;
            binlog_files.push(BinlogPart {
                symbol: member.symbol.clone(),
                part: state.part,
                started_utc: started_utc.clone(),
            });
            states.push(state);
        }
        let gaps_path = gaps_csv_path(root);
        ensure_gaps_csv(&gaps_path).map_err(|e| anyhow::anyhow!("{e:?}"))?;
        // Старый `stop` прошлой сессии этого каталога не должен остановить
        // новую на первом же тике.
        let _ = std::fs::remove_file(root.join("stop"));
        let deadline_ns = match plan {
            SessionPlan::Timed { minutes, .. } => Some(
                started_ns
                    + i64::try_from(minutes.saturating_mul(60)).unwrap_or(i64::MAX) * 1_000_000_000,
            ),
            SessionPlan::AlwaysOn => None,
        };
        let resources_pid = std::process::id();
        let pool_path = root.join("instruments.csv");
        let pool_mtime = file_mtime(&pool_path);
        Ok(Self {
            root: root.to_path_buf(),
            plan,
            started_ns,
            started_utc,
            start_hour_utc,
            deadline_ns,
            states,
            binlog_files,
            gaps_path,
            gaps: 0,
            reconnects: 0,
            resyncs: 0,
            unrouted: 0,
            session_json_dirty: false,
            parse_latencies_ns: LatencyHistogram::new(),
            queue_latencies_ns: LatencyHistogram::new(),
            samples: Arc::new(Mutex::new(Vec::new())),
            clock_samples: Arc::new(AtomicU64::new(0)),
            resources_pid,
            resources_start: sample_resources(resources_pid),
            resources_wall_start: std::time::Instant::now(),
            last_hourly_ns: started_ns,
            hours_reported: 0,
            pool_path,
            pool_mtime,
        })
    }

    /// Слежение за `<root>/instruments.csv` (таск 34): `mtime` не изменился
    /// — выход после одного `metadata()`. Изменился — файл перечитан
    /// целиком (`load_pool`, тот же разбор, что на старте); строки с
    /// символами, которые уже пишутся, пропущены (повтор той же строки
    /// ничего не дублирует); новые — файл части текущих суток UTC
    /// (`open_symbol_state`, следующая свободная часть), затем `feed.add`
    /// одной партией; индексы обязаны совпасть с позициями в `states` —
    /// иначе кадр ушёл бы в чужой файл. Ошибка чтения файла или строки —
    /// строка stderr, запись идёт, повтор на следующем изменении `mtime`.
    /// Удаление строки не поддерживается: запись символа продолжается.
    fn check_pool_file<F: DynamicPool + ?Sized>(&mut self, feed: &mut F, ts_ns: i64) {
        let mtime = file_mtime(&self.pool_path);
        if mtime == self.pool_mtime {
            return;
        }
        self.pool_mtime = mtime;
        if mtime.is_none() {
            return;
        }
        let pool = match load_pool(&self.pool_path) {
            Ok(pool) => pool,
            Err(e) => {
                eprintln!(
                    "session: {} изменился, но не прочитан: {e} — состав записи прежний, \
                     повтор при следующем изменении файла",
                    self.pool_path.display()
                );
                return;
            }
        };
        let mut fresh: Vec<PoolMember> = Vec::new();
        for member in pool {
            let known = self.states.iter().any(|s| s.member.symbol == member.symbol)
                || fresh.iter().any(|m| m.symbol == member.symbol);
            if !known {
                fresh.push(member);
            }
        }
        if fresh.is_empty() {
            return;
        }
        let day = match day_string_of_ns(ts_ns) {
            Ok(day) => day,
            Err(e) => {
                eprintln!("session: добавление отложено — сутки не вычислены: {e}");
                return;
            }
        };
        let mut opened: Vec<SymbolState> = Vec::with_capacity(fresh.len());
        for member in &fresh {
            match open_symbol_state(&self.root, member, &day) {
                Ok(state) => opened.push(state),
                Err(e) => {
                    eprintln!(
                        "session: {} не добавлен — файл части не открыт: {e}; \
                         повтор при следующем изменении {}",
                        member.symbol,
                        self.pool_path.display()
                    );
                    discard_opened(&self.root, &day, opened);
                    return;
                }
            }
        }
        let indices = match feed.add(fresh) {
            Ok(indices) => indices,
            Err(e) => {
                eprintln!(
                    "session: партия не добавлена — источник отказал: {e}; файлы частей \
                     удалены, повтор при следующем изменении {}",
                    self.pool_path.display()
                );
                discard_opened(&self.root, &day, opened);
                return;
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
            eprintln!(
                "session: партия не добавлена — источник вернул индексы {indices:?}, \
                 ожидались {expected:?}; файлы частей удалены, события этих индексов \
                 не пишутся"
            );
            discard_opened(&self.root, &day, opened);
            return;
        }
        let started_utc = ts_utc_of_ns(ts_ns);
        for state in opened {
            eprintln!(
                "session: добавлен {} (tick={}, step={}) — файл {}",
                state.member.symbol,
                state.member.tick_e9,
                state.member.step_e9,
                day_file_path(&self.root, &state.member.symbol, &day, state.part).display()
            );
            self.binlog_files.push(BinlogPart {
                symbol: state.member.symbol.clone(),
                part: state.part,
                started_utc: started_utc.clone(),
            });
            self.states.push(state);
        }
        self.session_json_dirty = false;
        self.write_session_json_or_log(ts_ns);
    }

    /// Строка `gaps.csv`; `symbol: None` — событие всей сессии (`session.json`
    /// не переписан), колонка `symbol` пустая.
    fn log_gap(&self, symbol: Option<usize>, kind: GapKind, ts_utc: String, detail: String) {
        let row = GapRow {
            ts_utc,
            symbol: symbol
                .and_then(|i| self.states.get(i))
                .map(|s| s.member.symbol.clone())
                .unwrap_or_default(),
            kind,
            detail,
        };
        let _ = append_gap_row(&self.gaps_path, &row);
    }

    /// Кадр одного инструмента на диск; неудача — счётчик, строка
    /// `gaps.csv` `write_failed` и очищенный батч (повтор той же записи в
    /// следующий кадр смешал бы порядок). Файл при этом остаётся на границе
    /// кадра (`FrameSink::flush`); если и откат не удался — часть закрыта,
    /// инструмент получает следующую часть тех же суток.
    fn flush_symbol(&mut self, idx: usize, now_ns: i64) {
        let Some(state) = self.states.get_mut(idx) else {
            return;
        };
        if let Err(e) = flush_symbol_batch(state) {
            let detail =
                format!("кадр не записался ({e}) — потеряно до {FRAME_TARGET_RECORDS} записей");
            self.log_gap(
                Some(idx),
                GapKind::WriteFailed,
                ts_utc_of_ns(now_ns),
                detail,
            );
            if self.states[idx].writer.get_ref().boundary_lost() {
                let day_index = self.states[idx].day_index;
                match self.open_next_part(idx, day_index, now_ns, now_ns) {
                    Ok(()) => eprintln!(
                        "session: {}: граница кадра потеряна — часть переоткрыта (p{})",
                        self.states[idx].member.symbol, self.states[idx].part
                    ),
                    Err(e) => {
                        let detail = format!("часть не переоткрыта после потери границы: {e}");
                        eprintln!("session: {}: {detail}", self.states[idx].member.symbol);
                        self.log_gap(
                            Some(idx),
                            GapKind::WriteFailed,
                            ts_utc_of_ns(now_ns),
                            detail,
                        );
                    }
                }
            }
        }
    }

    fn flush_all(&mut self, now_ns: i64) {
        for idx in 0..self.states.len() {
            self.flush_symbol(idx, now_ns);
        }
    }

    /// Тик таймера рантайма (таск 25): сброс кадров всех инструментов —
    /// окно потери `FRAME_LOSS_WINDOW_SECS` держится и при полной тишине;
    /// раз в `HOURLY_REFRESH_SECS` — `session.json` и одна строка stderr.
    /// `<root>/stop` — файл-команда остановки; удаляется на старте сессии,
    /// чтобы старый файл не остановил новую запись на первом тике.
    fn stop_path(&self) -> PathBuf {
        self.root.join("stop")
    }

    fn stop_requested(&self) -> bool {
        self.stop_path().is_file()
    }

    fn on_tick(&mut self, ts_ns: i64) {
        self.flush_all(ts_ns);
        let hourly_due = ts_ns - self.last_hourly_ns >= HOURLY_REFRESH_SECS as i64 * 1_000_000_000;
        // Отложенная ротацией запись (см. `open_next_part`) — одна на все
        // ротации этого тика; если тут же наступил час, пишет часовая ветка.
        if self.session_json_dirty && !hourly_due {
            self.session_json_dirty = false;
            self.write_session_json_or_log(ts_ns);
        }
        if hourly_due {
            self.session_json_dirty = false;
            self.last_hourly_ns = ts_ns;
            self.hours_reported += 1;
            let Some(summary) = self.write_session_json_or_log(ts_ns) else {
                return;
            };
            let last = summary.samples.last();
            eprintln!(
                "session: час {} — records={} bytes={} gaps={} reconnects={} resyncs={} \
                 frames_failed={} rss={} cpu={}",
                self.hours_reported,
                summary.records_total,
                summary.bytes_written,
                summary.gaps,
                summary.reconnects,
                summary.resyncs,
                summary.frames_failed,
                last.map_or("н/д".to_string(), |s| format!(
                    "{:.1} МиБ",
                    s.rss_bytes as f64 / (1024.0 * 1024.0)
                )),
                last.and_then(|s| s.cpu_pct)
                    .map_or("н/д".to_string(), |c| format!("{c:.1}%")),
            );
        }
    }

    /// Периодический `session.json` — best-effort: отказ `rename` (читатель
    /// живого каталога держит файл открытым без share-delete — ровно
    /// сценарий «анализ по накопленному») — строка stderr и `gaps.csv`,
    /// цикл продолжается, следующая попытка — через час или на первом
    /// тике после ротации суток.
    /// Один отказ не останавливает суточный коллектор без сброса.
    fn write_session_json_or_log(&mut self, ts_ns: i64) -> Option<SessionSummary> {
        match self.write_session_json(false) {
            Ok(summary) => Some(summary),
            Err(e) => {
                let detail = format!("session.json не переписан: {e}");
                eprintln!("session: {detail} — следующая попытка через час или на ротации");
                self.log_gap(None, GapKind::WriteFailed, ts_utc_of_ns(ts_ns), detail);
                None
            }
        }
    }

    /// Финализация на любом выходе из цикла (`None` от `Feed` — Ctrl+C или
    /// конец сценария, дедлайн `Timed`): сброс писателей и финальный
    /// `session.json` с `closed = true` — **до** чего угодно сетевого
    /// (`run_session`: замер часов после, best-effort; второй Ctrl+C во
    /// время ожидания NTP уже ничего не теряет).
    fn finalize(&mut self) -> anyhow::Result<SessionSummary> {
        let now_ns = SystemClock.now_ns();
        self.flush_all(now_ns);
        self.write_session_json(true)
    }

    /// Ротация по суткам UTC (таск 25, как `record::Recorder::ensure_day`):
    /// сутки события биржи **позже** суток файла инструмента — недописанный
    /// батч кадром в старые сутки, новая часть новых суток
    /// (`open_next_part`). Только вперёд: опоздавшее событие прошлых суток
    /// (сделка с `T` раньше `cts` уже принятой дельты) идёт в текущую
    /// часть — иначе части D/D+1 чередовались бы `-p2/-p3` на каждом таком
    /// событии. Отказ ротации — строка stderr и `gaps.csv`, события идут в
    /// текущую часть, повтор не раньше `FRAME_LOSS_WINDOW_SECS`; цикл не
    /// останавливается. Полночь — не разрыв, строки в `gaps.csv` нет.
    fn rotate_symbol_day(&mut self, idx: usize, exch_ts_ns: i64, local_ts_ns: i64) {
        let day_index = exch_ts_ns.div_euclid(NS_PER_DAY);
        let Some(state) = self.states.get(idx) else {
            return;
        };
        if day_index <= state.day_index || local_ts_ns < state.rotate_retry_after_ns {
            return;
        }
        self.flush_symbol(idx, local_ts_ns);
        if let Err(e) = self.open_next_part(idx, day_index, exch_ts_ns, local_ts_ns) {
            let state = &mut self.states[idx];
            state.rotate_retry_after_ns =
                local_ts_ns.saturating_add(FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000);
            let detail = format!(
                "ротация суток не удалась: {e} — события идут в часть p{} прежних суток, \
                 повтор через {FRAME_LOSS_WINDOW_SECS} с",
                state.part
            );
            eprintln!("session: {}: {detail}", state.member.symbol);
            self.log_gap(
                Some(idx),
                GapKind::WriteFailed,
                ts_utc_of_ns(local_ts_ns),
                detail,
            );
        }
    }

    /// Следующая свободная часть суток `day_index` для инструмента
    /// (`claim_part_with`, ничего не затирается): общий шов ротации по
    /// полуночи и переоткрытия после потерянной границы кадра. Первым
    /// кадром — синтетический снапшот книги, если она доверена (`synced`);
    /// иначе файл ждёт снапшота биржи, как при старте. `started_ns` —
    /// `started_utc` части в `binlog_files`; `session.json` переписывается
    /// сразу (best-effort) — `binlog_files` читают `profiles`/`watch`.
    fn open_next_part(
        &mut self,
        idx: usize,
        day_index: i64,
        started_ns: i64,
        local_ts_ns: i64,
    ) -> anyhow::Result<()> {
        let day = day_string_of_ns(day_index.saturating_mul(NS_PER_DAY))
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        let state = &mut self.states[idx];
        let (writer, part) = claim_symbol_binlog(
            &self.root,
            &state.member.symbol,
            &day,
            state.member.tick_e9,
            state.member.step_e9,
        )?;
        // Старый приёмник закрывается вместе с прежним `Writer` — его буфер
        // уже пуст после сброса у вызывающего.
        state.writer = writer;
        state.part = part;
        state.day_index = day_index;
        state.has_snapshot = false;
        state.batch.clear();
        self.binlog_files.push(BinlogPart {
            symbol: state.member.symbol.clone(),
            part,
            started_utc: ts_utc_of_ns(started_ns),
        });
        if state.synced {
            push_book_snapshot(state, started_ns, local_ts_ns);
            // Не `flush_symbol`: та на потерянной границе переоткрыла бы
            // часть снова — рекурсия на мёртвом диске.
            if let Err(e) = flush_symbol_batch(state) {
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

    /// Пишет `session.json` — через временный файл и `rename`, чтобы
    /// читатель живого каталога не застал полфайла.
    fn write_session_json(&self, closed: bool) -> anyhow::Result<SessionSummary> {
        let now_ns = SystemClock.now_ns();
        let duration_s =
            u64::try_from((now_ns - self.started_ns).max(0) / 1_000_000_000).unwrap_or(0);
        let samples = self.samples.lock().map(|v| v.clone()).unwrap_or_default();
        let resources_end = sample_resources(self.resources_pid);
        let (cpu_pct_avg, rss_bytes_start, rss_bytes_end) =
            match (self.resources_start, resources_end) {
                (Some((cpu0, rss0)), Some((cpu1, rss1))) => {
                    let wall_s = self.resources_wall_start.elapsed().as_secs_f64();
                    let avg = if wall_s > 0.0 {
                        Some((cpu1 - cpu0).max(0.0) / wall_s * 100.0)
                    } else {
                        None
                    };
                    (avg, Some(rss0), Some(rss1))
                }
                _ => (None, None, None),
            };
        let cpu_pct_max = samples.iter().filter_map(|s| s.cpu_pct).reduce(f64::max);
        let (pilot, pilot_minutes) = match self.plan {
            SessionPlan::Timed { pilot_minutes, .. } => (pilot_minutes.is_some(), pilot_minutes),
            SessionPlan::AlwaysOn => (false, None),
        };
        let summary = SessionSummary {
            started_utc: self.started_utc.clone(),
            start_hour_utc: self.start_hour_utc,
            duration_s,
            instruments: self
                .states
                .iter()
                .map(|s| s.member.symbol.clone())
                .collect(),
            records_total: self.states.iter().map(|s| s.records_written).sum(),
            gaps: self.gaps,
            clock_samples: self.clock_samples.load(Ordering::Relaxed),
            parse_p99_ns: self.parse_latencies_ns.percentile(99),
            queue_p99_ns: self.queue_latencies_ns.percentile(99),
            cpu_pct_avg,
            cpu_pct_max,
            rss_bytes_start,
            rss_bytes_end,
            out: self.root.clone(),
            debug: is_debug_session(duration_s),
            pilot,
            pilot_minutes,
            always_on: self.plan == SessionPlan::AlwaysOn,
            reconnects: self.reconnects,
            resyncs: self.resyncs,
            unrouted: self.unrouted,
            frames_failed: self.states.iter().map(|s| s.frames_failed).sum(),
            bytes_written: self
                .states
                .iter()
                .map(|s| s.writer.get_ref().bytes_written())
                .sum(),
            updated_utc: ts_utc_of_ns(now_ns),
            closed,
            samples,
            binlog_files: self.binlog_files.clone(),
        };
        let final_path = self.root.join("session.json");
        let tmp_path = self.root.join("session.json.tmp");
        std::fs::write(&tmp_path, serde_json::to_string_pretty(&summary)?)?;
        std::fs::rename(&tmp_path, &final_path)?;
        Ok(summary)
    }
}

/// Ядро прогона над любым `Feed` (A5): события → файлы, тики → сброс и
/// периодика, `None` → выход и финализация (`SessionCtx::finalize`: сброс
/// писателей, финальный `session.json` `closed = true`) — внутри шва, на
/// любом выходе из цикла. Периодические отказы (`session.json`, ротация,
/// кадр) цикл не останавливают — строка stderr и `gaps.csv`. Дедлайн
/// `Timed` проверяется на каждом событии **и тике** — при молчании пула
/// сессия сбора всё равно кончится не позже `FRAME_LOSS_WINDOW_SECS` после
/// срока.
fn run_session_loop<F: Feed + DynamicPool + ?Sized>(
    feed: &mut F,
    ctx: &mut SessionCtx,
) -> anyhow::Result<SessionSummary> {
    loop {
        if let Some(deadline_ns) = ctx.deadline_ns {
            if SystemClock.now_ns() >= deadline_ns {
                break;
            }
        }
        let Some(event) = feed.next_event() else {
            break;
        };
        match event {
            Event::Market {
                symbol,
                local_ts_ns,
                parse_latency_ns,
                payload,
            } => {
                if let Some(latency_ns) = parse_latency_ns {
                    let recv_ts_ns = SystemClock.now_ns();
                    ctx.parse_latencies_ns.record(latency_ns);
                    // `recv_ts_ns - local_ts_ns` — весь путь «recv() до
                    // потока решений»; вычитаем уже посчитанный чистый разбор
                    // (`latency_ns`), остаток — канал одного соединения,
                    // пересылка в общий канал, ожидание `blocking_recv`.
                    // `.max(0)` — не прячет отрицательный хвост, а не даёт
                    // редкому дребезгу часов (`SystemClock` не монотонны)
                    // испортить перцентиль отрицательным значением, которого
                    // очередь физически не может быть.
                    ctx.queue_latencies_ns
                        .record((recv_ts_ns - local_ts_ns - latency_ns).max(0));
                }
                let idx = symbol as usize;
                if idx >= ctx.states.len() {
                    continue;
                }
                if let Some(exch_ms) = event_exch_ms(&payload) {
                    ctx.rotate_symbol_day(idx, exch_ms.saturating_mul(1_000_000), local_ts_ns);
                }
                if let Err(e) = write_market_event(&mut ctx.states[idx], local_ts_ns, payload) {
                    let detail = format!(
                        "кадр не записался ({e:?}) — потеряно до {FRAME_TARGET_RECORDS} записей"
                    );
                    ctx.log_gap(
                        Some(idx),
                        GapKind::WriteFailed,
                        ts_utc_of_ns(local_ts_ns),
                        detail,
                    );
                }
            }
            Event::Gap {
                symbol,
                local_ts_ns,
                kind,
                detail,
            } => {
                // Неразрешённый маршрут — не строка `gaps.csv`: у неё
                // колонка `symbol` обязательна, а чей это кадр — как раз и
                // неизвестно. Считаем отдельно и печатаем в сводке.
                if kind == FeedGapKind::Unrouted {
                    ctx.unrouted += 1;
                    continue;
                }
                ctx.gaps += 1;
                let idx = symbol as usize;
                let record_kind = match kind {
                    FeedGapKind::Unrouted => unreachable!("отсеян выше"),
                    FeedGapKind::ParseFailed => GapKind::ParseError,
                    FeedGapKind::SequenceGap => {
                        ctx.resyncs += 1;
                        GapKind::SequenceGap
                    }
                    FeedGapKind::BookInvariant => {
                        ctx.resyncs += 1;
                        GapKind::BookInvariant
                    }
                    FeedGapKind::Disconnected => {
                        // Один разрыв сокета — одно переподключение, сколько
                        // бы инструментов сокет ни нёс (таск 28): копии по
                        // остальным инструментам приходят
                        // `DisconnectedSameSocket` и дают только свою строку
                        // `gaps.csv` и свой сброс `synced`.
                        ctx.reconnects += 1;
                        GapKind::SequenceGap
                    }
                    FeedGapKind::DisconnectedSameSocket => GapKind::SequenceGap,
                };
                if kind != FeedGapKind::ParseFailed {
                    if let Some(state) = ctx.states.get_mut(idx) {
                        state.synced = false;
                    }
                }
                ctx.log_gap(Some(idx), record_kind, ts_utc_of_ns(local_ts_ns), detail);
            }
            Event::Tick { local_ts_ns } => {
                ctx.on_tick(local_ts_ns);
                ctx.check_pool_file(feed, local_ts_ns);
                // Штатная остановка файлом (В-41): Ctrl+C доходит только из
                // настоящей консоли, а коллектор запускают из оболочек агента;
                // `<root>/stop` на тике — тот же выход, что `None` от `Feed`:
                // сброс писателей, `session.json closed=true`. Одна проверка
                // метаданных раз в `FRAME_LOSS_WINDOW_SECS`, не на событие.
                if ctx.stop_requested() {
                    eprintln!(
                        "session: найден {} — штатная остановка",
                        ctx.stop_path().display()
                    );
                    break;
                }
            }
        }
    }
    ctx.finalize()
}

pub fn run_session(args: &SessionArgs) -> anyhow::Result<SessionSummary> {
    let plan = resolve_duration(args)?;
    match plan {
        SessionPlan::Timed {
            pilot_minutes: Some(pm),
            ..
        } => eprintln!("session: pilot: {pm} мин (PLAN.md §11)"),
        SessionPlan::AlwaysOn => eprintln!(
            "session: always-on — до Ctrl+C; сброс кадров раз в {FRAME_LOSS_WINDOW_SECS} с, \
             session.json/clock.csv раз в {HOURLY_REFRESH_SECS} с и на остановке (В-34)"
        ),
        SessionPlan::Timed { .. } => {}
    }
    let pool = resolve_pool(args)?;
    let started_ns = SystemClock.now_ns();
    let mut ctx = SessionCtx::open(&args.root, &pool, plan, started_ns)?;
    // Первая запись `session.json` — сразу: живой каталог с первой минуты
    // выглядит сессией для `profiles`/`watch` (`binlog_files`, часы частей).
    ctx.write_session_json(false)?;
    // GC на десяти сразу: CPU, RSS по `resource_sample_period` — фоновый
    // поток, не в горячем пути; ряд — в `session.json.samples`, не в stderr
    // (таск 25).
    spawn_resource_sampler(ctx.samples.clone());
    spawn_clock_sampler(
        args.ntp_addr.clone(),
        args.base_url.clone(),
        args.root.join("clock.csv"),
        ctx.clock_samples.clone(),
    );

    let mut feed = LiveFeed::spawn_with_ticks(pool, Duration::from_secs(FRAME_LOSS_WINDOW_SECS))?;
    feed.stop_handle().stop_on_ctrl_c();
    // Писатели сброшены и `session.json` закрыт внутри цикла — до любого
    // сетевого вызова ниже: второй Ctrl+C (`exit(130)`) во время ожидания
    // NTP/REST (до ~12 с при упавшей сети) батчей уже не теряет.
    let mut summary = run_session_loop(&mut feed, &mut ctx)?;
    drop(feed);

    // Финальный замер часов — после цикла, не в нём (A9), best-effort;
    // удачный — ещё одна финальная запись, чтобы `clock_samples` был
    // точен; её отказ вердикт не меняет (первая финальная уже на диске).
    let idx = ctx.clock_samples.load(Ordering::Relaxed);
    if take_clock_sample(
        &args.ntp_addr,
        &args.base_url,
        idx,
        &args.root.join("clock.csv"),
    ) {
        ctx.clock_samples.fetch_add(1, Ordering::Relaxed);
        if let Ok(with_clock) = ctx.write_session_json(true) {
            summary = with_clock;
        }
    }

    if let Some(p99) = summary.parse_p99_ns {
        eprintln!(
            "session: разбор — p99 {:.1} мкс по {} кадрам (бюджет `PLAN.md` 3.1: < 200 мкс; \
             гистограмма, разрешение ~{:.1}%)",
            p99 as f64 / 1000.0,
            ctx.parse_latencies_ns.len(),
            LatencyHistogram::RESOLUTION_PCT
        );
    }
    if let Some(p99) = summary.queue_p99_ns {
        eprintln!(
            "session: очередь (разбор → поток решений) — p99 {:.1} мкс по {} кадрам",
            p99 as f64 / 1000.0,
            ctx.queue_latencies_ns.len()
        );
    }
    if let (Some(avg), Some(start), Some(end)) = (
        summary.cpu_pct_avg,
        summary.rss_bytes_start,
        summary.rss_bytes_end,
    ) {
        eprintln!(
            "session: CPU средний {avg:.1}% ядра (бюджет `PLAN.md` 6.1: < 5%); RSS начало \
             {:.1} МиБ, конец {:.1} МиБ; сэмплов {}; байт {}; reconnects={} resyncs={} \
             frames_failed={} unrouted={}",
            start as f64 / (1024.0 * 1024.0),
            end as f64 / (1024.0 * 1024.0),
            summary.samples.len(),
            summary.bytes_written,
            summary.reconnects,
            summary.resyncs,
            summary.frames_failed,
            summary.unrouted
        );
    }
    Ok(summary)
}

#[cfg(test)]
mod tests;
