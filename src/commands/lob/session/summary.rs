//! Запись о сессии — `session.json`: часть файла (`BinlogPart`), замер
//! ресурсов (`ResourceSample`) и сама сводка (`SessionSummary`). Отдельно —
//! это контракт для читателей (`profiles`/`watch`/`pilot`/`dashboard`), а не
//! логика записи; сам файл пишет `SessionCtx::write_session_json`.

use std::path::PathBuf;

/// Один файл-часть в `session.json.binlog_files` (таск 22, критерий
/// приёмки «перечисляет части и их `started_utc`»). Список накапливается
/// через все прогоны `lob session` в один `--root`: вторая сессия тех же
/// суток дописывает свои части к уже существующим (`run_session` перечитывает
/// прежний `session.json`, если он есть и разбирается) — тот же принцип
/// «ничего не затирается», что уже применяет `record::claim_part` к самим
/// бинлогам. Олвейс-он (таск 25) дописывает часть на каждой ротации суток.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct BinlogPart {
    pub symbol: String,
    pub part: u32,
    pub started_utc: String,
}

/// Один замер ресурсов процесса (таск 25, ревью таска 24: ряд в артефакте,
/// не в stderr). `cpu_pct` — `%` одного ядра между этим и предыдущим
/// замером; `None` у первого (не с чем сравнить).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ResourceSample {
    pub ts_utc: String,
    pub rss_bytes: u64,
    pub cpu_pct: Option<f64>,
}

/// Счётчики одного потока глубины за прогон (T45, критерий приёмки
/// «`session.json` несёт раздельные счётчики по потокам: записи, байты,
/// разрывы»). `depth` — глубина топика этого потока (`orderbook.<depth>`,
/// `bybit::conn::SUBSCRIBED_DEPTHS`): 50 — быстрый, файл в корне сессии;
/// 200 — глубокий, файл в подкаталоге `deep/`. Сумма `records`/`bytes` по
/// потокам равна `records_total`/`bytes_written` сводки — это те же числа,
/// разложенные по потокам, а не второй счёт.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct DepthCounters {
    pub depth: u32,
    /// Записей легло в файлы этого потока (по всем инструментам).
    pub records: u64,
    /// Байт на диске у файлов этого потока, заголовки включительно.
    pub bytes: u64,
    /// Разрывов `u`/инвариантов книги **этого** потока: разрыв `.200` не
    /// считается разрывом `.50`. Разрыв сокета, роняющий оба потока сразу,
    /// в это число не входит — он не принадлежит одному потоку
    /// (`events::Gap::depth = None`), и его по-прежнему считает
    /// `reconnects`.
    pub resyncs: u64,
    /// Кадров этого потока, не записавшихся на диск.
    pub frames_failed: u64,
}

/// Итог `lob session`: то, что печатается и что легло в запись о сессии.
/// `Deserialize` — не только для читателей, но и для самого `run_session`:
/// вторая сессия в те же сутки перечитывает предыдущий файл, чтобы
/// накопить `binlog_files`, не потеряв историю прежних частей.
#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct SessionSummary {
    pub started_utc: String,
    pub start_hour_utc: u32,
    /// Фактическая длительность на момент записи файла (таск 25: у
    /// олвейс-он растёт от часа к часу; у сессии сбора — окно целиком).
    pub duration_s: u64,
    pub instruments: Vec<String>,
    pub records_total: u64,
    pub gaps: u64,
    pub clock_samples: u64,
    /// `p99` длительности разбора одного кадра, наносекунды — `None`, если
    /// сессия не переслала ни одного `Message` от живого источника (сессия
    /// длиной 0 или сплошные `Gap`): перцентиль пустой выборки не число, а
    /// изобретённое значение (правило 1 `interfaces.md`), печатать нечего.
    pub parse_p99_ns: Option<i64>,
    /// `p99` времени между «кадр разобран» (`parsed_ts_ns`, метка в `bybit::
    /// conn::Connection::run`) и «кадр дошёл до потока решений»
    /// (`feed.next_event()` вернула его в `run_session`) — наносекунды.
    /// Таск 20: `parse_p99_ns` — синхронный разбор (`parsed_ts_ns -
    /// local_ts_ns`, ни одного `.await` между двумя метками, `bybit/conn.rs`
    /// строки вокруг `let local_ts_ns = clock.now_ns()` — комментарий на
    /// месте), очередь рантайма в него не входит уже сегодня; это поле —
    /// отдельный замер именно очереди (канал одного соединения → пересылка →
    /// общий канал → `blocking_recv` потока решений), чтобы не гадать, а
    /// назвать числом. `None` при пустой выборке, тем же правилом, что и
    /// `parse_p99_ns`.
    pub queue_p99_ns: Option<i64>,
    /// Средний и максимальный CPU (% одного ядра) за сессию — гейт GC «CPU <
    /// 5% ядра суммарно» (`PLAN.md` 6.1). `avg` — по двум концевым замерам
    /// кумулятивного CPU-времени процесса (`sample_resources`, точно на всё
    /// время сессии); `max` — по периодическим замерам ряда `samples`
    /// (`spawn_resource_sampler`: 30-с первый час, часовые дальше). `None`, если
    /// `sample_resources` недоступен на этой ОС.
    pub cpu_pct_avg: Option<f64>,
    pub cpu_pct_max: Option<f64>,
    /// RSS в начале и в конце сессии, байты — гейт GC «RSS раз в 30 с,
    /// плоский»: разница `rss_bytes_end - rss_bytes_start` и есть число,
    /// которым эта плоскость проверяется, а не оставляется читателю stderr.
    pub rss_bytes_start: Option<u64>,
    pub rss_bytes_end: Option<u64>,
    pub out: PathBuf,
    /// `true`, пока `duration_s` держится в отладочной фазе (`< 3600` с —
    /// час, тот же порог, что `commands::lob::DEFAULT_REPEAT_WINDOW_MS`
    /// (3 600 000 мс) уже называет окном повторов, не второе изобретённое
    /// число). У олвейс-он коллектора становится `false` с первого часа
    /// (`CLAUDE.md`: «любой тестовый прогон — не дольше 5 минут (фаза
    /// отладки, результат — не данные)»).
    pub debug: bool,
    /// Таск 22: `true`, когда сессия — пилот §11 (`--pilot-minutes`), не
    /// сессия сбора (`--minutes`). `pilot` называет режим CLI, `debug` —
    /// фазу проекта (`is_debug_session`), не одно и то же измерение.
    #[serde(default)]
    pub pilot: bool,
    /// Длина пилота в минутах, если `pilot`; `None` у обычной сессии сбора.
    #[serde(default)]
    pub pilot_minutes: Option<u32>,
    /// Таск 25: `true` у олвейс-он коллектора (`--always-on`).
    #[serde(default)]
    pub always_on: bool,
    /// Переподключений транспорта за прогон (`Gap::Disconnected`) и ресинков
    /// книги снапшотом (`Gap::SequenceGap`/`BookInvariant`) — таск 25: без
    /// них сутки записи нечем оценить; каждый — строка `gaps.csv`.
    #[serde(default)]
    pub reconnects: u64,
    /// Рыночных кадров, чей топик не сопоставлен ни одному инструменту
    /// своего сокета (таск 28, `bybit::conn::ConnEvent::Unrouted`). Строки
    /// `gaps.csv` у них нет — неизвестно, чьи это данные, а колонка
    /// `symbol` там обязательна; поэтому счётчик. В норме ноль.
    #[serde(default)]
    pub unrouted: u64,
    /// Отказов `connect()` за прогон (K1, 2026-09-17): сокет не открылся —
    /// ни `reconnects`, ни `frames_failed` такого не показывают, а устойчивый
    /// `403`/`429` до этой правки не давал ни строки `gaps.csv`, ни счётчика.
    #[serde(default)]
    pub connect_failed: u64,
    #[serde(default)]
    pub resyncs: u64,
    /// Кадров, не записавшихся на диск (сумма по инструментам) — таск 25.
    #[serde(default)]
    pub frames_failed: u64,
    /// Байт на диске по всем частям этого прогона (заголовки включительно)
    /// — байт/мин и байт/запись считаются отсюда, не `du`.
    #[serde(default)]
    pub bytes_written: u64,
    /// Момент этой записи файла (RFC 3339) и признак финальной: `closed =
    /// false` у периодических (час, ротация), `true` — после остановки.
    #[serde(default)]
    pub updated_utc: String,
    #[serde(default)]
    pub closed: bool,
    /// Ряд RSS/CPU (`spawn_resource_sampler`): раз в `RESOURCE_SAMPLE_SECS`
    /// первый час, дальше раз в `HOURLY_REFRESH_SECS` (`resource_sample_
    /// period`) — вердикт «плоский» стоит на этом ряду в артефакте (ревью
    /// таска 24), и ряд не растёт без потолка на олвейс-он.
    #[serde(default)]
    pub samples: Vec<ResourceSample>,
    /// Части, записанные во все прогоны `lob session` в этот `--root`, по
    /// порядку появления (не по порядку чтения `session_binlog_for` — та
    /// сортирует по имени файла, эта хронология по факту вызовов). Пусто у
    /// файлов старого формата (`#[serde(default)]` — таск 17 уже принял
    /// этот приём для обратной совместимости `ready.flag`).
    #[serde(default)]
    pub binlog_files: Vec<BinlogPart>,
    /// Раздельные счётчики потоков глубины (T45), по элементу на поток
    /// (`bybit::conn::SUBSCRIBED_DEPTHS`): быстрый `.50` первым, глубокий
    /// `.200` вторым. Пусто у записей старого формата (`#[serde(default)]`) —
    /// читатель старых `session.json` не обязан их знать.
    #[serde(default)]
    pub streams: Vec<DepthCounters>,
}
