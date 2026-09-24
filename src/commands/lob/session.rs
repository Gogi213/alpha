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
//! одна строка сводки. Остановка — `None` от `Feed` (Ctrl+C и, на Unix,
//! SIGTERM через `feed::live::StopHandle` — A8.2: `systemctl stop`/`restart`
//! и перезагрузка шлют SIGTERM; тот же путь, что файл `<root>/stop` и
//! сигнал-заменитель в тестах).
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
mod parts;
mod pool;
mod pool_watch;
mod resources;
mod sink;
mod summary;

pub use args::{
    SessionArgs, SessionPlan, MAX_MINUTES, MAX_PILOT_MINUTES, MIN_MINUTES, MIN_PILOT_MINUTES,
};
pub use summary::{BinlogPart, DepthCounters, PoolRemoval, ResourceSample, SessionSummary};

use args::resolve_duration;
use pool::resolve_pool;
use resources::{
    hour_utc_of_ns, sample_resources, spawn_clock_sampler, spawn_resource_sampler,
    take_clock_sample,
};
use sink::{
    event_stream, flush_symbol_batch, write_market_event, LatencyHistogram, SymbolState,
    STREAM_COUNT,
};

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::bybit::conn::{Clock, SystemClock, FAST_STREAM};
use crate::commands::record::{
    append_gap_row, day_string_of_ns, ensure_gaps_csv, event_exch_ms, gaps_csv_path, ts_utc_of_ns,
    GapKind, GapRow, FRAME_LOSS_WINDOW_SECS, FRAME_TARGET_RECORDS, HOURLY_REFRESH_SECS,
};
use crate::feed::live::{LiveFeed, PoolMember};
use crate::feed::{DynamicPool, Event, Feed, GapDetail, GapKind as FeedGapKind};

/// Подкаталог глубокого потока в корне сессии:
/// `<root>/deep/<SYMBOL>-<день>.binlog` (T45). Именно подкаталог, а не
/// суффикс имени: все существующие резолверы и читатели (`session_binlog_for`
/// и всё, что ходит `root/*.binlog`, а также `dashboard`/`watch`/`verify`)
/// читают **корень** сессии, и файл, лежащий глубже, они не видят —
/// поведение прежних команд не меняется (критерий приёмки T45).
pub(super) const DEEP_DIR: &str = "deep";

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
    /// Эпизодов молчания рынка за прогон (V5, доработка 2026-09-24,
    /// `feed::GapKind::MarketSilence`): дольше `recv_timeout` нет `Book`/
    /// `Trade`, соединение живо и не рвётся (решение владельца) — один на
    /// эпизод, снимается первым же рыночным событием.
    silence_episodes: u64,
    /// Наибольшая тишина среди всех эпизодов прогона, наносекунды —
    /// обновляется на каждом тике пинга, пока эпизод не кончился (не только
    /// на первом срабатывании), поэтому отражает фактическую длину, включая
    /// эпизод, ещё идущий на момент записи сводки. `None`, пока не было ни
    /// одного эпизода — считать «самая долгая тишина — 0» без единого
    /// замера значило бы изобретённое число (правило 1 `interfaces.md`), а
    /// не измеренное.
    silence_max_ns: Option<i64>,
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
    /// Снятие применено, а добавление новой партии отказало (A8.1b): состав
    /// приводится к файлу **повторно на следующем тике**, не дожидаясь нового
    /// `mtime` — правка пула одноразовая, и потерять из-за разового отказа
    /// источника монету до следующей правки нельзя. Снимается, как только
    /// перечитка прошла целиком.
    pool_retry: bool,
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
            let state = parts::open_symbol_state(root, member, &day)?;
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
        let pool_mtime = pool_watch::file_mtime(&pool_path);
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
            silence_episodes: 0,
            silence_max_ns: None,
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
            pool_retry: false,
        })
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

    fn on_tick<C: Clock>(&mut self, ts_ns: i64, clock: &C) {
        self.flush_all(ts_ns);
        let hourly_due = ts_ns - self.last_hourly_ns >= HOURLY_REFRESH_SECS as i64 * 1_000_000_000;
        // Отложенная ротацией запись (см. `open_next_part`) — одна на все
        // ротации этого тика; если тут же наступил час, пишет часовая ветка.
        if self.session_json_dirty && !hourly_due {
            self.session_json_dirty = false;
            self.write_session_json_or_log(ts_ns, clock);
        }
        if hourly_due {
            self.session_json_dirty = false;
            self.last_hourly_ns = ts_ns;
            self.hours_reported += 1;
            let Some(summary) = self.write_session_json_or_log(ts_ns, clock) else {
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
    fn write_session_json_or_log<C: Clock>(
        &mut self,
        ts_ns: i64,
        clock: &C,
    ) -> Option<SessionSummary> {
        match self.write_session_json(false, clock) {
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
    /// время ожидания NTP уже ничего не теряет). `clock` — не конкретный
    /// `SystemClock` (ремонт W6, ревью 23.09/18.09 «`SystemClock.now_ns()`
    /// мимо трейта»): композиция выбирает реализацию в `run_session`, здесь —
    /// только трейт, как в `bybit::conn::Connection::run`.
    fn finalize<C: Clock>(&mut self, clock: &C) -> anyhow::Result<SessionSummary> {
        let now_ns = clock.now_ns();
        self.flush_all(now_ns);
        self.write_session_json(true, clock)
    }

    /// Пишет `session.json` — через временный файл и `rename`, чтобы
    /// читатель живого каталога не застал полфайла. Сама сводка —
    /// `build_summary` (`session/summary.rs`, W6): здесь только запись на
    /// диск, не расчёт.
    fn write_session_json<C: Clock>(
        &self,
        closed: bool,
        clock: &C,
    ) -> anyhow::Result<SessionSummary> {
        let summary = self.build_summary(closed, clock);
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
fn run_session_loop<F: Feed + DynamicPool + ?Sized, C: Clock>(
    feed: &mut F,
    ctx: &mut SessionCtx,
    clock: &C,
) -> anyhow::Result<SessionSummary> {
    loop {
        if let Some(deadline_ns) = ctx.deadline_ns {
            if clock.now_ns() >= deadline_ns {
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
            } => on_market(ctx, clock, symbol, local_ts_ns, parse_latency_ns, payload),
            Event::Gap {
                symbol,
                local_ts_ns,
                kind,
                depth,
                silence_ns,
                first_of_episode,
                detail,
            } => on_gap(
                ctx,
                GapEvent {
                    symbol,
                    local_ts_ns,
                    kind,
                    depth,
                    silence_ns,
                    first_of_episode,
                    detail,
                },
            ),
            Event::Tick { local_ts_ns } => {
                ctx.on_tick(local_ts_ns, clock);
                ctx.check_pool_file(feed, local_ts_ns, clock);
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
    ctx.finalize(clock)
}

/// `Event::Market`: разбор латентности, ротация суток по потоку события и
/// сама запись кадра. Вынесено из `run_session_loop` (W6, ревью 23.09) —
/// тело ветки `Market` не при цикле, а сама по себе законченная операция.
/// `clock` — трейт (см. doc `SessionCtx::finalize`), не конкретный
/// `SystemClock`.
fn on_market<C: Clock>(
    ctx: &mut SessionCtx,
    clock: &C,
    symbol: u16,
    local_ts_ns: i64,
    parse_latency_ns: Option<i64>,
    payload: crate::bybit::ws::Event,
) {
    if let Some(latency_ns) = parse_latency_ns {
        let recv_ts_ns = clock.now_ns();
        ctx.parse_latencies_ns.record(latency_ns);
        // `recv_ts_ns - local_ts_ns` — весь путь «recv() до потока решений»;
        // вычитаем уже посчитанный чистый разбор (`latency_ns`), остаток —
        // канал одного соединения, пересылка в общий канал, ожидание
        // `blocking_recv`. `.max(0)` — не прячет отрицательный хвост, а не
        // даёт редкому дребезгу часов (`SystemClock` не монотонны) испортить
        // перцентиль отрицательным значением, которого очередь физически не
        // может быть.
        ctx.queue_latencies_ns
            .record((recv_ts_ns - local_ts_ns - latency_ns).max(0));
    }
    let idx = symbol as usize;
    // Снятый на ходу символ (A8.1): сокет остановлен, но события, успевшие
    // лечь в канал, доходят и после снятия — писать их некуда (файлы
    // закрыты), а журнал и `session.json` считают по состояниям, а не по
    // событиям.
    if !ctx.is_active(idx) {
        return;
    }
    // Поток события — один раз на событие (`sink::event_stream`: книга по
    // глубине топика, сделка всегда быстрый): и ротация суток, и запись
    // спрашивают его же, а не каждая своё.
    if let Some(stream) = event_stream(&payload) {
        if let Some(exch_ms) = event_exch_ms(&payload) {
            ctx.rotate_symbol_day(idx, stream, exch_ms.saturating_mul(1_000_000), local_ts_ns);
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

/// Поля `Event::Gap` одним значением — `on_gap` берёт `ctx` и это, а не
/// семь отдельных параметров (`clippy::too_many_arguments`, W6).
struct GapEvent {
    symbol: u16,
    local_ts_ns: i64,
    kind: FeedGapKind,
    depth: Option<u32>,
    silence_ns: Option<i64>,
    first_of_episode: bool,
    detail: GapDetail,
}

/// `Event::Gap`: один `match` на все варианты `feed::GapKind`, без
/// `unreachable!` (ремонт W6, ревью 23.09/18.09: раньше `Unrouted`/
/// `MarketSilence` отсеивались `continue` до отдельного `match`, а тот всё
/// равно был обязан перечислить их панической веткой — регресс в новом
/// варианте причины ловился бы только паникой в проде, не типом). `_ if
/// !ctx.is_active(idx)` — единый барьер «символ снят с записи» для всех
/// причин **кроме** `Unrouted`/`MarketSilence`: они не привязаны к
/// конкретному инструменту записи так, как остальные (счётчик обязан расти,
/// даже если символ только что снят). `record_kind = None` — как раньше
/// `continue`: ни строки `gaps.csv`, ни `ctx.gaps`, ни сброса `synced`.
fn on_gap(ctx: &mut SessionCtx, event: GapEvent) {
    let GapEvent {
        symbol,
        local_ts_ns,
        kind,
        depth,
        silence_ns,
        first_of_episode,
        detail,
    } = event;
    let idx = symbol as usize;
    let slot = depth.and_then(sink::stream_of_depth);
    let record_kind = match kind {
        // Неразрешённый маршрут — не строка `gaps.csv`: у неё колонка
        // `symbol` обязательна, а чей это кадр — как раз и неизвестно.
        // Считаем отдельно и печатаем в сводке.
        FeedGapKind::Unrouted => {
            ctx.unrouted += 1;
            None
        }
        // Тишина рынка (V5, доработка 2026-09-24) — не потеря кадра и не шов
        // покрытия: соединение по ней не рвётся (решение владельца), книга
        // инструмента остаётся доверенной. Строки `gaps.csv` не даёт, тем же
        // приёмом, что `Unrouted`; несёт только счётчик эпизодов и максимум
        // длительности. Событие приходит на каждом тике пинга, пока эпизод
        // не кончился (`bybit::conn::Connection::run_with_backoff`) —
        // счётчик растёт только на первом (`first_of_episode`), максимум
        // обновляется на каждом, включая ещё не кончившийся эпизод,
        // застигнутый на записи текущей сводки.
        FeedGapKind::MarketSilence => {
            if first_of_episode {
                ctx.silence_episodes += 1;
            }
            if let Some(ns) = silence_ns {
                ctx.silence_max_ns = Some(ctx.silence_max_ns.map_or(ns, |m| m.max(ns)));
            }
            None
        }
        // Снятый на ходу символ (A8.1): разрыв, пришедший после снятия, —
        // следствие нашей же остановки сокета (снятие идёт на тике, события
        // канала упорядочены), а не потеря данных, которую надо считать
        // швом: файлы символа уже закрыты.
        _ if !ctx.is_active(idx) => None,
        FeedGapKind::ParseFailed => Some(GapKind::ParseError),
        // Разрыв — по потоку (T45): `slot` — слот разорванного потока,
        // `None` — потеря, которая потоку не принадлежит (разрыв сокета
        // роняет оба потока сразу). `gap_record_kind` — общее место счёта
        // ресинка для `SequenceGap`/`BookInvariant` (раньше два одинаковых
        // блока).
        FeedGapKind::SequenceGap => Some(gap_record_kind(ctx, slot, GapKind::SequenceGap)),
        FeedGapKind::BookInvariant => Some(gap_record_kind(ctx, slot, GapKind::BookInvariant)),
        FeedGapKind::Disconnected => {
            // Один разрыв сокета — одно переподключение, сколько бы
            // инструментов сокет ни нёс (таск 28): копии по остальным
            // инструментам приходят `DisconnectedSameSocket` и дают только
            // свою строку `gaps.csv` и свой сброс `synced`.
            ctx.reconnects += 1;
            Some(GapKind::SequenceGap)
        }
        FeedGapKind::DisconnectedSameSocket => Some(GapKind::SequenceGap),
        FeedGapKind::ConnectFailed => {
            // Сокет не открылся: ни книги, ни `synced` сбрасывать не надо
            // (их и не было), но потеря обязана быть видна — счётчик и
            // строка `gaps.csv` (K1, 2026-09-17).
            ctx.connect_failed += 1;
            Some(GapKind::ConnectFailed)
        }
        FeedGapKind::SubscribeFailed => {
            // A8.3: биржа не согласовала топик — у инструмента не будет
            // данных; счётчик и строка `gaps.csv`. Книга этого инструмента
            // недоверена (снапшот не приходил) — общий сброс `synced` ниже
            // это и делает.
            ctx.subscribe_failed += 1;
            Some(GapKind::SubscribeFailed)
        }
    };
    let Some(record_kind) = record_kind else {
        return;
    };
    ctx.gaps += 1;
    // Сброс доверия: у разрыва потока — только его книга, у потери без
    // потока (разрыв сокета) — обе книги инструмента.
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
    // Строка собирается здесь, а не в `Feed` (ремонт W2, ревью 23.09):
    // `detail` — данные (`feed::GapDetail`), не готовый текст;
    // `Unrouted`/`MarketSilence` не доходят до этой строки — их `Display`
    // не зовётся вовсе.
    ctx.log_gap(
        Some(idx),
        record_kind,
        ts_utc_of_ns(local_ts_ns),
        detail.to_string(),
    );
}

/// Общий счёт ресинка книги (`SequenceGap`/`BookInvariant`, W6, ревью
/// 23.09: раньше два одинаковых блока `ctx.resyncs += 1; if let Some(slot) =
/// slot { ctx.resyncs_by_stream[slot] += 1; }` внутри `match` в `on_gap`).
fn gap_record_kind(ctx: &mut SessionCtx, slot: Option<usize>, kind: GapKind) -> GapKind {
    ctx.resyncs += 1;
    if let Some(slot) = slot {
        ctx.resyncs_by_stream[slot] += 1;
    }
    kind
}

pub fn run_session(args: &SessionArgs) -> anyhow::Result<SessionSummary> {
    let plan = resolve_duration(args)?;
    match plan {
        SessionPlan::Timed {
            pilot_minutes: Some(pm),
            ..
        } => eprintln!("session: pilot: {pm} мин (PLAN.md §11)"),
        SessionPlan::AlwaysOn => eprintln!(
            "session: always-on — до Ctrl+C/SIGTERM (на сервере — файл stop или systemctl stop); \
             сброс кадров раз в {FRAME_LOSS_WINDOW_SECS} с, \
             session.json/clock.csv раз в {HOURLY_REFRESH_SECS} с и на остановке (В-34)"
        ),
        SessionPlan::Timed { .. } => {}
    }
    let pool = resolve_pool(args)?;
    let started_ns = SystemClock.now_ns();
    let mut ctx = SessionCtx::open(&args.root, &pool, plan, started_ns)?;
    // Первая запись `session.json` — сразу: живой каталог с первой минуты
    // выглядит сессией для `profiles`/`watch` (`binlog_files`, часы частей).
    ctx.write_session_json(false, &SystemClock)?;
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
    // Остановка — Ctrl+C и (на Unix, A8.2) SIGTERM: `systemctl stop/restart` и
    // перезагрузка шлют SIGTERM, и он идёт тем же штатным путём, что файл `stop`.
    feed.stop_handle().stop_on_signals();
    // Писатели сброшены и `session.json` закрыт внутри цикла — до любого
    // сетевого вызова ниже: второй Ctrl+C (`exit(130)`) во время ожидания
    // NTP/REST (до ~12 с при упавшей сети) батчей уже не теряет.
    let mut summary = run_session_loop(&mut feed, &mut ctx, &SystemClock)?;
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
        if let Ok(with_clock) = ctx.write_session_json(true, &SystemClock) {
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
             frames_failed={} unrouted={} connect_failed={} subscribe_failed={} \
             silence_episodes={} silence_max_ns={}",
            start as f64 / (1024.0 * 1024.0),
            end as f64 / (1024.0 * 1024.0),
            summary.samples.len(),
            summary.bytes_written,
            summary.reconnects,
            summary.resyncs,
            summary.frames_failed,
            summary.unrouted,
            summary.connect_failed,
            summary.subscribe_failed,
            summary.silence_episodes,
            summary
                .silence_max_ns
                .map_or("н/д".to_string(), |ns| ns.to_string())
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
    ///
    /// Находка V4/№1 аудита 18.09 (`docs/findings/audit-2026-09-18.md:68,451`):
    /// этот тест сознательно не банил `SystemClock`, но пять прямых вызовов
    /// `SystemClock.now_ns()` в решающем цикле (`run_session_loop`, `finalize`,
    /// `write_session_json`) сами мимо теста не ловились никем. Ремонт W6
    /// (ревью 23.09): те три функции и `on_market` теперь `fn f<C: Clock>(...,
    /// clock: &C, ...)`, как `bybit::conn::Connection::run`; `SystemClock` —
    /// только в `run_session()`, композиционном корне, который его и создаёт.
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
