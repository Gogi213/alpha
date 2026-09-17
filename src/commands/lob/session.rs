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
//! Пул на ходу (таск 34 — добавление, A8.1 — снятие): `<root>/instruments.csv`
//! — живой файл. На тике (не чаще `FRAME_LOSS_WINDOW_SECS`) один `metadata()`;
//! изменился `mtime` — файл перечитан тем же `load_pool` (и только если он
//! дописан: разобрался и кончается переводом строки — `pool_file_is_complete`),
//! символы, которых ещё нет в `states`, получают файл части текущих суток и
//! своё соединение (`feed::DynamicPool::add`); индексы обязаны продолжить
//! `states`. Строки, которых в файле больше нет, снимаются с записи
//! (`feed::DynamicPool::remove` + `close_symbol`): писатели обоих потоков
//! сбрасываются и закрываются, состояние остаётся в `states` с `active = false`
//! (индексы не переиспользуются — вернувшееся в файл имя получает новое
//! состояние и новую часть), события и разрывы снятого индекса, доехавшие из
//! канала, цикл пропускает, а факт снятия виден в `session.json.pool_removals`.
//! Замена монеты — те же два шага за одну перечитку.
//!
//! **Два потока глубины (T45).** Сессия ведёт по два файла на инструмент:
//! основной `<root>/<SYMBOL>-<день>.binlog` — быстрый поток `orderbook.50`,
//! ровно как до T45 (и в том же каталоге, чтобы существующие резолверы и
//! команды видели его тем же `session_binlog_for`), и глубокий
//! `<root>/deep/<SYMBOL>-<день>.binlog` — поток `orderbook.200` тем же форматом
//! v3 (В-49: раскладка не меняется, свой синтетический снапшот первым кадром).
//! Поток события выбирает `sink::event_stream` (глубина из топика); книги,
//! `u`-контроль, ресинк, ротация суток, кадры и счётчики — раздельные; в
//! `session.json` они сведены в `streams` (записи, байты, разрывы).

mod args;
mod pool;
mod resources;
mod sink;
mod summary;

pub use args::{
    SessionArgs, SessionPlan, MAX_MINUTES, MAX_PILOT_MINUTES, MIN_MINUTES, MIN_PILOT_MINUTES,
};
pub(crate) use sink::FrameSink;
pub use summary::{BinlogPart, DepthCounters, PoolRemoval, ResourceSample, SessionSummary};

use args::{is_debug_session, resolve_duration};
use pool::{load_pool, resolve_pool};
use resources::{
    hour_utc_of_ns, sample_resources, spawn_clock_sampler, spawn_resource_sampler,
    take_clock_sample,
};
use sink::{
    event_stream, flush_symbol_batch, push_book_snapshot, write_market_event, LatencyHistogram,
    StreamState, SymbolState, STREAM_COUNT,
};

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::binlog::Writer;
use crate::bybit::conn::{Clock, SystemClock, DEEP_STREAM, FAST_STREAM, SUBSCRIBED_DEPTHS};
use crate::commands::record::{
    append_gap_row, claim_part_with, day_file_path, day_string_of_ns, ensure_gaps_csv,
    event_exch_ms, gaps_csv_path, ts_utc_of_ns, GapKind, GapRow, FRAME_LOSS_WINDOW_SECS,
    FRAME_TARGET_RECORDS, HOURLY_REFRESH_SECS, NS_PER_DAY,
};
use crate::feed::live::{LiveFeed, PoolMember};
use crate::feed::{DynamicPool, Event, Feed, GapKind as FeedGapKind};

/// Подкаталог глубокого потока в корне сессии:
/// `<root>/deep/<SYMBOL>-<день>.binlog` (T45). Именно подкаталог, а не
/// суффикс имени: все существующие резолверы и читатели (`session_binlog_for`
/// и всё, что ходит `root/*.binlog`, а также `dashboard`/`watch`/`verify`)
/// читают **корень** сессии, и файл, лежащий глубже, они не видят —
/// поведение прежних команд не меняется (критерий приёмки T45).
pub(super) const DEEP_DIR: &str = "deep";

/// Каталог файлов потока: быстрый `.50` — корень сессии (ровно как до T45),
/// глубокий `.200` — подкаталог `DEEP_DIR`. Единственное место, где эти два
/// каталога сопоставлены слотам `SUBSCRIBED_DEPTHS`.
fn stream_dir(root: &Path, stream: usize) -> PathBuf {
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
fn claim_symbol_binlog(
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
fn open_symbol_state(root: &Path, member: &PoolMember, day: &str) -> anyhow::Result<SymbolState> {
    let day_index = crate::commands::record::day_index_of_day_str(day)
        .map_err(|e| anyhow::anyhow!("{}: {e}", member.symbol))?;
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

/// Партия не состоялась (таск 34): закрыть уже открытые части и убрать их
/// файлы, чтобы в каталоге не остались пустые заголовки, а повтор не получил
/// `-p2`. Файлов теперь два на инструмент — по одному на поток; пути
/// собираются **до** `drop`, а удаление идёт после закрытия писателей (тот же
/// порядок и та же причина, что в `cleanup_failed_open`). Сам подкаталог
/// `deep/` не удаляется: пустой каталог никому не мешает, а его создание —
/// работа следующей попытки.
fn discard_opened(root: &Path, day: &str, opened: Vec<SymbolState>) {
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
    /// Снятия с записи на ходу (A8.1), в порядке появления — то, что уйдёт
    /// в `session.json.pool_removals`. Отдельным списком, а не выводом из
    /// `states`: состояние символа помнит только «снят/нет» (этого хватает
    /// горячему пути), а читателю нужна хронология, включая повторные
    /// снятия одного имени.
    pool_removals: Vec<PoolRemoval>,
    gaps_path: PathBuf,
    gaps: u64,
    reconnects: u64,
    resyncs: u64,
    /// Разрывов `u`/инвариантов книги **по потокам** (T45): индекс — слот
    /// `SUBSCRIBED_DEPTHS`. Отдельно от `resyncs` (всего), потому что
    /// разрыв `u` есть у конкретного потока — разрыв `.200` не имеет права
    /// считаться разрывом `.50`, а суммарное число нужно прежним читателям
    /// `session.json`.
    resyncs_by_stream: [u64; STREAM_COUNT],
    unrouted: u64,
    /// Отказов `connect()` за прогон (K1, 2026-09-17): сокет не открылся —
    /// ни `reconnects`, ни `frames_failed` этого не показывают, а устойчивый
    /// `403`/`429` до этой правки не давал вообще ничего.
    connect_failed: u64,
    /// Отказов подписки за прогон (A8.3, 2026-09-18): биржа не согласовала
    /// топик инструмента. До этой правки отказ не покидал соединение вовсе —
    /// инструмент молча стоял без данных.
    subscribe_failed: u64,
    /// Сколько строк `gaps.csv` не удалось записать (V6, 2026-09-17).
    /// `AtomicU64`, а не `u64`: `log_gap` зовётся из `&self`-контекстов
    /// (в том числе там, где `&mut self` занят другим полем), а счётчик
    /// обязан расти в любом из них — иначе отказ журнала потерь исчезнет
    /// ровно в том сценарии, ради которого заведён.
    gap_rows_failed: AtomicU64,
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
fn pool_file_is_complete(path: &Path) -> bool {
    std::fs::read(path)
        .map(|bytes| bytes.last() == Some(&b'\n'))
        .unwrap_or(false)
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
            // В `binlog_files` — только быстрый поток (см. doc
            // `open_next_part`): это карта основных файлов каталога, и её
            // читают резолверы корня.
            binlog_files.push(BinlogPart {
                symbol: member.symbol.clone(),
                part: state.streams[FAST_STREAM].part,
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
            pool_removals: Vec::new(),
            gaps_path,
            gaps: 0,
            reconnects: 0,
            resyncs: 0,
            resyncs_by_stream: [0; STREAM_COUNT],
            unrouted: 0,
            connect_failed: 0,
            subscribe_failed: 0,
            gap_rows_failed: AtomicU64::new(0),
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
    /// запись идёт, повтор на следующем изменении `mtime`.
    fn check_pool_file<F: DynamicPool + ?Sized>(&mut self, feed: &mut F, ts_ns: i64) {
        let mtime = file_mtime(&self.pool_path);
        if mtime == self.pool_mtime {
            return;
        }
        self.pool_mtime = mtime;
        if mtime.is_none() {
            return;
        }
        if !pool_file_is_complete(&self.pool_path) {
            eprintln!(
                "session: {} изменён, но не дописан (нет перевода строки в конце) — \
                 состав записи прежний, повтор при следующем изменении файла; пул пиши \
                 атомарно (временный файл + переименование) или дописывай строки",
                self.pool_path.display()
            );
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
        // Снятие (A8.1) — до добавления: замена монеты это «убрать» и
        // «добавить» за одну перечитку, а снятый индекс (и имя) не должен
        // мешать возврату того же символа в пул (он получит новый индекс).
        let removed: Vec<usize> = self
            .states
            .iter()
            .enumerate()
            .filter(|(_, s)| s.active && !pool.iter().any(|m| m.symbol == s.member.symbol))
            .map(|(idx, _)| idx)
            .collect();
        let mut removed_any = false;
        if !removed.is_empty() {
            let indices: Vec<u16> = removed
                .iter()
                .map(|&idx| u16::try_from(idx).unwrap_or(u16::MAX))
                .collect();
            if let Err(e) = feed.remove(&indices) {
                eprintln!(
                    "session: снятие не состоялось — источник отказал: {e}; состав записи \
                     прежний, повтор при следующем изменении {}",
                    self.pool_path.display()
                );
                return;
            }
            for &idx in &removed {
                self.close_symbol(idx, ts_ns);
            }
            removed_any = true;
        }
        let mut fresh: Vec<PoolMember> = Vec::new();
        for member in pool {
            // «Уже пишется» — только про **активные** состояния: снятый
            // символ, вернувшийся в файл, добавляется заново (новый индекс,
            // новая часть, своё соединение).
            let known = self
                .states
                .iter()
                .any(|s| s.active && s.member.symbol == member.symbol)
                || fresh.iter().any(|m| m.symbol == member.symbol);
            if !known {
                fresh.push(member);
            }
        }
        if fresh.is_empty() {
            if !removed_any {
                return;
            }
            // Снятие без добавления: в файлах и `states` уже ничего не
            // меняется, а `session.json` обязан показать снятие.
            self.session_json_dirty = false;
            self.write_session_json_or_log(ts_ns);
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
        self.write_session_json_or_log(ts_ns);
    }

    /// Инструмент пишется сейчас (`false` — снят с записи на ходу, A8.1).
    /// Индексы не переиспользуются, поэтому события снятого символа,
    /// успевшие лечь в канал до остановки сокета, доходят и после снятия —
    /// их надо пропустить (журнал и `session.json` считают по состояниям,
    /// а не по событиям), а не писать в закрытый файл.
    fn is_active(&self, idx: usize) -> bool {
        matches!(self.states.get(idx), Some(s) if s.active)
    }

    /// Снимает инструмент с записи (A8.1): кадры обоих потоков сбрасываются
    /// и файлы закрываются (`FrameSink::close` — дескриптор отпущен), символ
    /// помечается снятым. История остаётся: состояние живёт в `states` до
    /// конца сессии, `session.json.instruments`/`binlog_files` его помнят
    /// (и `bytes_written` тоже — счётчики приёмника закрытие не трогает), а
    /// `pool_removals` называет момент. Отказ сброса кадра — строка
    /// `gaps.csv` `write_failed`, снятие продолжается: молча потерять кадр
    /// нельзя, а оставить символ в записи из-за отказа диска — значит
    /// обещать то, чего нет.
    fn close_symbol(&mut self, idx: usize, now_ns: i64) {
        let Some(symbol) = self.states.get(idx).map(|s| s.member.symbol.clone()) else {
            return;
        };
        let ts_utc = ts_utc_of_ns(now_ns);
        let mut failed: Vec<String> = Vec::new();
        if let Some(state) = self.states.get_mut(idx) {
            state.active = false;
            for stream in &mut state.streams {
                if let Err(e) = flush_symbol_batch(stream) {
                    failed.push(format!("поток .{}: {e}", stream.depth));
                }
                stream.writer.get_mut().close();
            }
        }
        for detail in failed {
            eprintln!("session: {symbol}: {detail} — кадр не записался при снятии с записи");
            self.log_gap(
                Some(idx),
                GapKind::WriteFailed,
                ts_utc.clone(),
                format!(
                    "кадр не записался при снятии инструмента с записи: {detail} — потеряно \
                     до {FRAME_TARGET_RECORDS} записей"
                ),
            );
        }
        eprintln!("session: снят {symbol} — файлы потоков сброшены и закрыты ({ts_utc})");
        self.pool_removals.push(PoolRemoval { symbol, ts_utc });
    }

    /// Строка `gaps.csv`; `symbol: None` — событие всей сессии (`session.json`
    /// не переписан), колонка `symbol` пустая.
    ///
    /// Отказ записи считается (V6, 2026-09-17): журнал потерь сам может не
    /// писаться — полный диск гасит и его, — и тогда единственным следствием
    /// остаётся счётчик `session.json.gap_rows_failed`. Печатается только
    /// первый отказ: если диск кончился, строк будет столько же, сколько
    /// потерь, и заливать этим stderr нельзя.
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
        if append_gap_row(&self.gaps_path, &row).is_err()
            && self.gap_rows_failed.fetch_add(1, Ordering::Relaxed) == 0
        {
            eprintln!(
                "session: строка gaps.csv не записана ({}) — журнал потерь тоже не пишется; \
                 счётчик отказов в session.json.gap_rows_failed",
                self.gaps_path.display()
            );
        }
    }

    /// Кадр одного потока одного инструмента на диск; неудача — счётчик,
    /// строка `gaps.csv` `write_failed` и очищенный батч (повтор той же
    /// записи в следующий кадр смешал бы порядок). Файл при этом остаётся на
    /// границе кадра (`FrameSink::flush`); если и откат не удался — часть
    /// закрыта, поток получает следующую часть тех же суток. `stream` —
    /// слот потока (`sink::event_stream`), не «инструмент целиком»: у
    /// быстрого и глубокого свои файлы и свой `boundary_lost`.
    fn flush_symbol(&mut self, idx: usize, stream: usize, now_ns: i64) {
        let Some(state) = self.states.get_mut(idx) else {
            return;
        };
        if !state.active {
            // Снятый с записи инструмент (A8.1): писатели закрыты, кадров
            // у него не осталось — сбрасывать нечего.
            return;
        }
        if let Err(e) = flush_symbol_batch(&mut state.streams[stream]) {
            let depth = state.streams[stream].depth;
            let detail = format!(
                "поток .{depth}: кадр не записался ({e}) — потеряно до {FRAME_TARGET_RECORDS} \
                 записей"
            );
            self.log_gap(
                Some(idx),
                GapKind::WriteFailed,
                ts_utc_of_ns(now_ns),
                detail,
            );
            if self.states[idx].streams[stream]
                .writer
                .get_ref()
                .boundary_lost()
            {
                let day_index = self.states[idx].streams[stream].day_index;
                match self.open_next_part(idx, stream, day_index, now_ns, now_ns) {
                    Ok(()) => eprintln!(
                        "session: {}: поток .{depth}: граница кадра потеряна — часть \
                         переоткрыта (p{})",
                        self.states[idx].member.symbol, self.states[idx].streams[stream].part
                    ),
                    Err(e) => {
                        let detail = format!(
                            "поток .{depth}: часть не переоткрыта после потери границы: {e}"
                        );
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
            for stream in 0..STREAM_COUNT {
                self.flush_symbol(idx, stream, now_ns);
            }
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
    ///
    /// Ротация — **по потоку события** (T45): `.200` и `.50` живут своими
    /// частями, и сутки одного не тянут за собой файл другого. Сутки
    /// сравниваются с индексом суток файла своего потока.
    fn rotate_symbol_day(&mut self, idx: usize, stream: usize, exch_ts_ns: i64, local_ts_ns: i64) {
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
    fn open_next_part(
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
            instruments: {
                // По одному разу на имя, в порядке первого появления:
                // снятый и вернувшийся символ (A8.1) — это два состояния, но
                // один инструмент в записи, и дубликата в списке быть не
                // должно (читатели идут по `instruments`, как по набору).
                let mut out: Vec<String> = Vec::with_capacity(self.states.len());
                for state in &self.states {
                    if !out.iter().any(|s| s == &state.member.symbol) {
                        out.push(state.member.symbol.clone());
                    }
                }
                out
            },
            records_total: self
                .states
                .iter()
                .flat_map(|s| s.streams.iter())
                .map(|s| s.records_written)
                .sum(),
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
            connect_failed: self.connect_failed,
            subscribe_failed: self.subscribe_failed,
            gap_rows_failed: self.gap_rows_failed.load(Ordering::Relaxed),
            frames_failed: self
                .states
                .iter()
                .flat_map(|s| s.streams.iter())
                .map(|s| s.frames_failed)
                .sum(),
            bytes_written: self
                .states
                .iter()
                .flat_map(|s| s.streams.iter())
                .map(|s| s.writer.get_ref().bytes_written())
                .sum(),
            // Раздельные счётчики по потокам (T45, критерий приёмки):
            // записи, байты и разрывы каждого потока отдельно — по ним
            // видно, что `.200` действительно пишется своим файлом, а не
            // растворяется в сумме.
            streams: (0..STREAM_COUNT)
                .map(|slot| summary::DepthCounters {
                    depth: SUBSCRIBED_DEPTHS[slot],
                    records: self
                        .states
                        .iter()
                        .map(|s| s.streams[slot].records_written)
                        .sum(),
                    bytes: self
                        .states
                        .iter()
                        .map(|s| s.streams[slot].writer.get_ref().bytes_written())
                        .sum(),
                    resyncs: self.resyncs_by_stream[slot],
                    frames_failed: self
                        .states
                        .iter()
                        .map(|s| s.streams[slot].frames_failed)
                        .sum(),
                })
                .collect(),
            updated_utc: ts_utc_of_ns(now_ns),
            closed,
            samples,
            binlog_files: self.binlog_files.clone(),
            pool_removals: self.pool_removals.clone(),
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
                // Снятый на ходу символ (A8.1): сокет остановлен, но
                // события, успевшие лечь в канал, доходят и после снятия —
                // писать их некуда (файлы закрыты), а журнал и `session.json`
                // считают по состояниям, а не по событиям.
                if !ctx.is_active(idx) {
                    continue;
                }
                // Поток события — один раз на событие (`sink::event_stream`:
                // книга по глубине топика, сделка всегда быстрый): и ротация
                // суток, и запись спрашивают его же, а не каждая своё.
                if let Some(stream) = event_stream(&payload) {
                    if let Some(exch_ms) = event_exch_ms(&payload) {
                        ctx.rotate_symbol_day(
                            idx,
                            stream,
                            exch_ms.saturating_mul(1_000_000),
                            local_ts_ns,
                        );
                    }
                    if let Err(e) = write_market_event(&mut ctx.states[idx], local_ts_ns, payload) {
                        let depth = ctx.states[idx].streams[stream].depth;
                        let detail = format!(
                            "поток .{depth}: кадр не записался ({e:?}) — потеряно до \
                             {FRAME_TARGET_RECORDS} записей"
                        );
                        ctx.log_gap(
                            Some(idx),
                            GapKind::WriteFailed,
                            ts_utc_of_ns(local_ts_ns),
                            detail,
                        );
                    }
                }
            }
            Event::Gap {
                symbol,
                local_ts_ns,
                kind,
                depth,
                detail,
            } => {
                // Неразрешённый маршрут — не строка `gaps.csv`: у неё
                // колонка `symbol` обязательна, а чей это кадр — как раз и
                // неизвестно. Считаем отдельно и печатаем в сводке.
                if kind == FeedGapKind::Unrouted {
                    ctx.unrouted += 1;
                    continue;
                }
                let idx = symbol as usize;
                // Снятый на ходу символ (A8.1): разрыв, пришедший после
                // снятия, — следствие нашей же остановки сокета (снятие идёт
                // на тике, события канала упорядочены), а не потеря данных,
                // которую надо считать швом: файлы символа уже закрыты.
                if !ctx.is_active(idx) {
                    continue;
                }
                ctx.gaps += 1;
                // Разрыв — по потоку (T45): `sink::stream_of_depth` даёт слот
                // разорванного потока, `None` — потеря, которая потоку не
                // принадлежит (разрыв сокета роняет оба потока сразу,
                // неразобранный кадр не несёт ни символа, ни глубины).
                // Счётчик разрывов и сброс доверия — раздельные.
                let slot = depth.and_then(sink::stream_of_depth);
                let record_kind = match kind {
                    FeedGapKind::Unrouted => unreachable!("отсеян выше"),
                    FeedGapKind::ParseFailed => GapKind::ParseError,
                    FeedGapKind::SequenceGap => {
                        ctx.resyncs += 1;
                        if let Some(slot) = slot {
                            ctx.resyncs_by_stream[slot] += 1;
                        }
                        GapKind::SequenceGap
                    }
                    FeedGapKind::BookInvariant => {
                        ctx.resyncs += 1;
                        if let Some(slot) = slot {
                            ctx.resyncs_by_stream[slot] += 1;
                        }
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
                    FeedGapKind::ConnectFailed => {
                        // Сокет не открылся: ни книги, ни `synced` сбрасывать не
                        // надо (их и не было), но потеря обязана быть видна —
                        // счётчик и строка `gaps.csv` (K1, 2026-09-17).
                        ctx.connect_failed += 1;
                        GapKind::ConnectFailed
                    }
                    FeedGapKind::SubscribeFailed => {
                        // A8.3: биржа не согласовала топик — у инструмента не
                        // будет данных; счётчик и строка `gaps.csv`. Книга
                        // этого инструмента недоверена (снапшот не приходил) —
                        // общий сброс `synced` ниже это и делает.
                        ctx.subscribe_failed += 1;
                        GapKind::SubscribeFailed
                    }
                };
                // Сброс доверия: у разрыва потока — только его книга, у
                // потери без потока (разрыв сокета) — обе книги инструмента.
                if kind != FeedGapKind::ParseFailed {
                    if let Some(state) = ctx.states.get_mut(idx) {
                        match slot {
                            Some(slot) => state.streams[slot].synced = false,
                            None => {
                                for stream in &mut state.streams {
                                    stream.synced = false;
                                }
                            }
                        }
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
             frames_failed={} unrouted={} connect_failed={} subscribe_failed={}",
            start as f64 / (1024.0 * 1024.0),
            end as f64 / (1024.0 * 1024.0),
            summary.samples.len(),
            summary.bytes_written,
            summary.reconnects,
            summary.resyncs,
            summary.frames_failed,
            summary.unrouted,
            summary.connect_failed,
            summary.subscribe_failed
        );
    }
    Ok(summary)
}

#[cfg(test)]
mod hot_path_guard {
    /// C3 аудита 2026-09-17: у `session.rs` греп-теста горячего пути не было, и
    /// регресс (`HashMap` по символу, стенные часы мимо трейта `Clock`) ловило
    /// бы только ревью. Здесь проверяются ровно те запреты, которые этому
    /// файлу применимы:
    ///
    /// - `HashMap`/`BTreeMap` — запрет 7 (лукап по символу идёт по `u16`);
    /// - `SystemTime::now` — стенные часы мимо трейта: время берётся `Clock`
    ///   (`SystemClock` и есть его реализация, поэтому его имя не банится);
    /// - `Instant::now` разрешён и не банится: модуль меряет им **длительности**
    ///   (ресурсы, интервалы), а не время события; строки собраны из частей,
    ///   иначе литерал триггерил бы проверку сам на себе.
    #[test]
    fn event_loop_has_no_maps_and_no_wall_clock_outside_the_trait() {
        const SRC: &str = include_str!("session.rs");
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

#[cfg(test)]
mod tests;
