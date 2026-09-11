//! `lob session` — сессия по всему пулу одновременно (таск 04, история 7).
//!
//! Тонкая оболочка поверх `feed::live::LiveFeed`: сама сессия не решает,
//! откуда идут события — она держит `&mut dyn Feed` и не отличила бы живой
//! сокет от `feed::replay::ReplayFeed`, если бы получила его вместо (это и
//! есть критерий приёмки «вызывающий код не различает», `interfaces.md`).
//! Один поток решений читает `Feed::next_event()` в цикле, без `async`,
//! без `tokio` в этой функции — только вызов, который сам блокируется на
//! канале (`ARCHITECTURE.md` A3).

use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Args;

use crate::binlog::{Header, Record, Writer};
use crate::book::{Book, Side};
use crate::bybit::clock::{append_row, sample, BybitServerTimeSource, ClockRow, UdpNtpSource};
use crate::bybit::conn::{Clock, SystemClock};
use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::commands::record::{
    append_gap_row, ensure_gaps_csv, gaps_csv_path, GapKind, GapRow, ZSTD_LEVEL,
};
use crate::feed::live::{LiveFeed, PoolMember};
use crate::feed::{Event, Feed};

/// Граница `--minutes` — брифу владельца дословно: «данные набираются
/// короткими сессиями — от пяти до пятнадцати минут по всему пулу
/// одновременно» (история 7, `R38`, `docs/plan/BUSINESS-TASK.md`). Не
/// умолчание и не изобретённое число этого файла — предел приходит из
/// текста задачи, не из кода.
pub const MIN_MINUTES: u64 = 5;
pub const MAX_MINUTES: u64 = 15;

/// Аргументы `lob session`. Ни у `minutes`, ни у путей нет правдоподобного
/// умолчания (правило 1 `interfaces.md`: параметр без умолчания лучше
/// изобретённого) — пул и место записи владелец называет каждый раз.
#[derive(Debug, Args)]
pub struct SessionArgs {
    /// `instruments.csv` последнего `lob pick` — колонки `symbol,tick_size,
    /// min_order_qty,qty_step,min_notional_value` (`CLAUDE.md`, «грабли»).
    #[arg(long)]
    pub pool_instruments: PathBuf,
    /// Корень сессии: по файлу `<SYMBOL>-<день UTC старта>.binlog` на
    /// инструмент (то же имя, что читают `verify`/`levels`/`markout` —
    /// `commands::record::day_file_path`), `gaps.csv`, `clock.csv`, запись
    /// о сессии.
    #[arg(long)]
    pub root: PathBuf,
    /// Длина сессии. Диапазон `MIN_MINUTES..=MAX_MINUTES` — решение
    /// владельца (история 7), не умолчание этого файла; `run_session`
    /// отклоняет значения вне него до всякой сети.
    #[arg(long)]
    pub minutes: u64,
    /// REST-хост Bybit v5 для одного замера `serverTime` в `clock.csv`.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// NTP-эталон для того же замера. Публичный пул `pool.ntp.org` — то же
    /// эксплуатационное решение хоста, что уже подразумевает `ASSUMPTION
    /// H12` («часы дисциплинируются NTP»), без выбора конкретного адреса.
    #[arg(long, default_value = "pool.ntp.org:123")]
    pub ntp_addr: String,
}

/// Одна строка `symbol,tick_size,...` из `instruments.csv` — читаем только
/// то, что нужно сессии, тик и шаг размера, тем же способом, что
/// `commands::record::load_steps_for_symbol` читает для одного символа.
#[derive(Debug, serde::Deserialize)]
struct InstrumentRow {
    symbol: String,
    tick_size: String,
    qty_step: String,
}

fn load_pool(path: &Path) -> anyhow::Result<Vec<PoolMember>> {
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок и падал здесь же.
    let mut r = super::pick::instruments_csv_reader(path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let mut pool = Vec::new();
    for row in r.deserialize::<InstrumentRow>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        let tick_e9 = crate::bybit::ws::parse_e9(row.tick_size.trim())
            .ok_or_else(|| anyhow::anyhow!("{}: tick_size не разобрался", row.symbol))?;
        let step_e9 = crate::bybit::ws::parse_e9(row.qty_step.trim())
            .ok_or_else(|| anyhow::anyhow!("{}: qty_step не разобрался", row.symbol))?;
        pool.push(PoolMember {
            symbol: row.symbol,
            tick_e9,
            step_e9,
        });
    }
    if pool.is_empty() {
        anyhow::bail!("{}: пуст — сначала `lob pick`", path.display());
    }
    Ok(pool)
}

/// Состояние одного инструмента пула: своя книга, свой файл. Индекс в этом
/// `Vec` — тот же `symbol: u8`, которым `Feed` метит каждое событие
/// (`interfaces.md`: тег события — не строка, лукап по строке на каждое
/// событие был бы `HashMap` на пути события, запрет 7).
struct SymbolState {
    member: PoolMember,
    book: Book,
    writer: Writer<std::fs::File>,
    records_written: u64,
    /// Скретч-буфер `write_market_event` — переиспользуется на каждое
    /// событие вместо `Vec::new()`, иначе горячий путь аллоцирует ровно там,
    /// где гейт GC требует ноль (`interfaces.md`, запрет 1; было ТУПИКОМ 1
    /// таска 04). Ёмкость с запасом на самый крупный кадр этого потока —
    /// `orderbook.50` снапшот обеих сторон, 50+50 записей; `.clear()` в
    /// начале `write_market_event` не освобождает ёмкость, только длину.
    scratch: Vec<Record>,
}

fn hftbacktest_flags(side: Side, is_snapshot: bool) -> u64 {
    use hftbacktest::types::{
        LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
        LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    };
    match (side, is_snapshot) {
        (Side::Bid, true) => LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
        (Side::Bid, false) => LOCAL_BID_DEPTH_EVENT,
        (Side::Ask, true) => LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
        (Side::Ask, false) => LOCAL_ASK_DEPTH_EVENT,
    }
}

/// Итог `lob session`: то, что печатается и что легло в запись о сессии.
#[derive(Debug, serde::Serialize)]
pub struct SessionSummary {
    pub started_utc: String,
    pub start_hour_utc: u32,
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
    /// время сессии); `max` — по периодическим замерам раз в 30 с
    /// (`spawn_resource_sampler`), тем же способом, что уже печатался в
    /// stderr. `None`, если `sample_resources` недоступен на этой ОС.
    pub cpu_pct_avg: Option<f64>,
    pub cpu_pct_max: Option<f64>,
    /// RSS в начале и в конце сессии, байты — гейт GC «RSS раз в 30 с,
    /// плоский»: разница `rss_bytes_end - rss_bytes_start` и есть число,
    /// которым эта плоскость проверяется, а не оставляется читателю stderr.
    pub rss_bytes_start: Option<u64>,
    pub rss_bytes_end: Option<u64>,
    pub out: PathBuf,
    /// `true`, пока `--minutes` держится в отладочной фазе (`< 3600` с —
    /// час, тот же порог, что `commands::lob::DEFAULT_REPEAT_WINDOW_MS`
    /// (3 600 000 мс) уже называет окном повторов, не второе изобретённое
    /// число). Сегодня `MAX_MINUTES = 15` не даёт `duration_s` дорасти до
    /// часа — поле всегда `true` на этой границе; `false` существует на
    /// случай, если владелец поднимет потолок для боевого сбора (`CLAUDE.md`:
    /// «любой тестовый прогон — не дольше 5 минут (фаза отладки, результат —
    /// не данные)»).
    pub debug: bool,
}

/// Отладочная сессия — короче часа. Чистая функция от `duration_s`, а не
/// прямая проверка `args.minutes < 60` внутри `run_session`: так у неё есть
/// собственный тест на обе ветки (`< 3600` и `>= 3600`), даже пока
/// `MAX_MINUTES = 15` не даёт второй ветке случиться через CLI.
fn is_debug_session(duration_s: u64) -> bool {
    const HOUR_S: u64 = 3600;
    duration_s < HOUR_S
}

/// Дописывает один часовой замер в `clock.csv` — best-effort: сеть (NTP,
/// REST) недоступна вне событийного пути этой функции (A9), и провал
/// замера не роняет сессию, только не даёт строки. Критерий приёмки — «не
/// реже раза за сессию», поэтому вызывается один раз до цикла и один раз
/// после.
fn take_clock_sample(args: &SessionArgs, idx: u64, clock_csv: &Path) -> anyhow::Result<bool> {
    let mut ntp = match UdpNtpSource::connect(args.ntp_addr.as_str(), Duration::from_secs(2)) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("session: clock — NTP {} недоступен: {e:?}", args.ntp_addr);
            return Ok(false);
        }
    };
    let mut bybit = match BybitServerTimeSource::new(args.base_url.clone()) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("session: clock — Bybit serverTime недоступен: {e:?}");
            return Ok(false);
        }
    };
    let row: ClockRow = sample(idx, &SystemClock, &mut ntp, &mut bybit);
    append_row(clock_csv, &row).map_err(|e| anyhow::anyhow!("{e:?}"))?;
    Ok(true)
}

/// Час старта UTC (история 8, `R41`) — колонка запись о сессии обязана
/// нести. `chrono` уже в зависимостях (`interfaces.md`, §1).
fn hour_utc_of_ns(ts_ns: i64) -> u32 {
    let secs = ts_ns.div_euclid(1_000_000_000);
    let nanos = ts_ns.rem_euclid(1_000_000_000) as u32;
    chrono::DateTime::from_timestamp(secs, nanos)
        .map(|dt| chrono::Timelike::hour(&dt))
        .unwrap_or(0)
}

/// Путь файла сессии символа — то же имя, часть 1 (`day_file_path(.., 1)`),
/// что и суточный файл `lob record`: `<root>/<SYMBOL>-<день UTC старта>.binlog`.
/// Сессия не ротирует файл посреди себя (`R38`: 5–15 минут разом), поэтому
/// части 2+ здесь не бывает. Чистая функция дня от `started_ns` — вынесена
/// из `run_session`, чтобы имя проверялось без сети (таск 19, находка G4:
/// `verify`/`levels`/`markout` искали `<SYMBOL>-<день>.binlog` там, где
/// сессия писала `<SYMBOL>.binlog` без даты).
fn session_binlog_path(root: &Path, symbol: &str, started_ns: i64) -> anyhow::Result<PathBuf> {
    let day = crate::commands::record::day_string_of_ns(started_ns)
        .map_err(|e| anyhow::anyhow!("{e}"))?;
    Ok(crate::commands::record::day_file_path(
        root, symbol, &day, 1,
    ))
}

pub fn run_session(args: &SessionArgs) -> anyhow::Result<SessionSummary> {
    if args.minutes < MIN_MINUTES || args.minutes > MAX_MINUTES {
        anyhow::bail!(
            "--minutes обязан быть в {MIN_MINUTES}..={MAX_MINUTES} (история 7, R38: «от пяти \
             до пятнадцати минут»): получено {}",
            args.minutes
        );
    }
    std::fs::create_dir_all(&args.root)?;
    let pool = load_pool(&args.pool_instruments)?;
    // GC на десяти сразу: CPU, RSS раз в 30 с (критерий приёмки таска 04,
    // handoff-04-1 ТУПИК 4). Фоновый поток, не в горячем пути — печатает и
    // копит `%` для `cpu_pct_max`; `run_session` его не ждёт.
    let cpu_samples = spawn_resource_sampler();
    // Концевые замеры (таск 20): RSS «в начале/конце» и средний CPU за всю
    // сессию — `(cpu_end - cpu_start) / wall_s`, точнее, чем усреднение
    // периодических `%` сэмплера, потому что не теряет неполные интервалы на
    // краях пятиминутного окна.
    let resources_pid = std::process::id();
    let resources_start = sample_resources(resources_pid);
    let resources_wall_start = std::time::Instant::now();

    // День решается один раз, до открытия файлов: `verify`/`levels`/
    // `markout` (`bybit::verify::run_verify`, `mod.rs::replay_symbol_*`)
    // ищут `<SYMBOL>-<день>.binlog` тем же префиксным поиском, что читает
    // `lob record` (таск 19, `interfaces.md` «Из таска 19»); называть файл
    // без даты означало бы, что эти команды не находят свежую сессию без
    // ручного переименования (слепая приёмка G4).
    let started_ns = SystemClock.now_ns();
    let started_utc = crate::commands::record::ts_utc_of_ns(started_ns);
    let start_hour_utc = hour_utc_of_ns(started_ns);

    let mut states: Vec<SymbolState> = Vec::with_capacity(pool.len());
    for member in &pool {
        let path = session_binlog_path(&args.root, &member.symbol, started_ns)?;
        let file = std::fs::File::create(&path)?;
        let header = Header {
            tick_e9: member.tick_e9,
            step_e9: member.step_e9,
            max_records_per_frame: crate::commands::record::MAX_RECORDS_PER_FRAME,
        };
        let writer =
            Writer::create(file, header, ZSTD_LEVEL).map_err(|e| anyhow::anyhow!("{e:?}"))?;
        states.push(SymbolState {
            member: member.clone(),
            book: Book::new(member.tick_e9, member.step_e9),
            writer,
            records_written: 0,
            // 50 бид + 50 аск — самый крупный кадр потока (`orderbook.50`
            // снапшот); запас, чтобы `.push` внутри `write_market_event`
            // не перевыделял на первом же снапшоте.
            scratch: Vec::with_capacity(128),
        });
    }

    let gaps_path = gaps_csv_path(&args.root);
    ensure_gaps_csv(&gaps_path).map_err(|e| anyhow::anyhow!("{e:?}"))?;
    let clock_path = args.root.join("clock.csv");

    let mut clock_samples: u64 = 0;
    if take_clock_sample(args, 0, &clock_path)? {
        clock_samples += 1;
    }

    let mut feed = LiveFeed::spawn(pool.clone());
    let deadline_ns = started_ns
        + i64::try_from(args.minutes.saturating_mul(60)).unwrap_or(i64::MAX) * 1_000_000_000;
    let mut gaps: u64 = 0;
    // Суббюджет «разбор» (`PLAN.md` 3.1, `p99 < 200 мкс`) — критерий приёмки
    // таска 04 «задержка разбора». Одно значение на кадр (`feed::Event::
    // Market::parse_latency_ns`, `Some` только в живом режиме); `p99`
    // считается по `bybit::probe::percentile_ns` — тот же перцентиль
    // «ближайший ранг», что уже мерит RTT, не второй расчёт того же самого.
    let mut parse_latencies_ns: Vec<i64> = Vec::new();
    // Очередь (таск 20, критерий 2): «от разбора до потока решений» —
    // отдельно от «разбора» самого по себе. Метка ставится тем же
    // `SystemClock`, что и `local_ts_ns`/`parsed_ts_ns` внутри `bybit::conn`
    // (`LiveFeed::spawn` подаёт `SystemClock` явно — один домен часов, не
    // второй, см. `spawn_with_clock`), сразу как только `feed.next_event()`
    // вернула событие потоку решений.
    let mut queue_latencies_ns: Vec<i64> = Vec::new();

    while SystemClock.now_ns() < deadline_ns {
        let Some(event) = feed.next_event() else {
            break;
        };
        let recv_ts_ns = SystemClock.now_ns();
        match event {
            Event::Market {
                symbol,
                local_ts_ns,
                parse_latency_ns,
                payload,
            } => {
                if let Some(latency_ns) = parse_latency_ns {
                    parse_latencies_ns.push(latency_ns);
                    // `recv_ts_ns - local_ts_ns` — весь путь «recv() до
                    // потока решений»; вычитаем уже посчитанный чистый разбор
                    // (`latency_ns`), остаток — канал одного соединения,
                    // пересылка в общий канал, ожидание `blocking_recv`.
                    // `.max(0)` — не прячет отрицательный хвост, а не даёт
                    // редкому дребезгу часов (`SystemClock` не монотонны)
                    // испортить перцентиль отрицательным значением, которого
                    // очередь физически не может быть.
                    queue_latencies_ns.push((recv_ts_ns - local_ts_ns - latency_ns).max(0));
                }
                let Some(state) = states.get_mut(symbol as usize) else {
                    continue;
                };
                write_market_event(state, local_ts_ns, payload);
            }
            Event::Gap {
                symbol,
                local_ts_ns,
                detail,
            } => {
                gaps += 1;
                let sym_name = states
                    .get(symbol as usize)
                    .map(|s| s.member.symbol.clone())
                    .unwrap_or_default();
                let ts_utc = crate::commands::record::ts_utc_of_ns(local_ts_ns);
                let row = GapRow {
                    ts_utc,
                    symbol: sym_name,
                    kind: GapKind::ParseError,
                    detail,
                };
                let _ = append_gap_row(&gaps_path, &row);
            }
        }
    }

    if take_clock_sample(args, 1, &clock_path)? {
        clock_samples += 1;
    }

    let mut records_total = 0u64;
    for state in &mut states {
        state.writer.flush()?;
        records_total += state.records_written;
    }

    let parse_p99_ns = if parse_latencies_ns.is_empty() {
        None
    } else {
        Some(crate::bybit::probe::percentile_ns(&parse_latencies_ns, 99))
    };
    if let Some(p99) = parse_p99_ns {
        eprintln!(
            "session: разбор — p99 {:.1} мкс по {} кадрам (бюджет `PLAN.md` 3.1: < 200 мкс)",
            p99 as f64 / 1000.0,
            parse_latencies_ns.len()
        );
    }
    let queue_p99_ns = if queue_latencies_ns.is_empty() {
        None
    } else {
        Some(crate::bybit::probe::percentile_ns(&queue_latencies_ns, 99))
    };
    if let Some(p99) = queue_p99_ns {
        eprintln!(
            "session: очередь (разбор → поток решений) — p99 {:.1} мкс по {} кадрам",
            p99 as f64 / 1000.0,
            queue_latencies_ns.len()
        );
    }

    let resources_end = sample_resources(resources_pid);
    let (cpu_pct_avg, rss_bytes_start, rss_bytes_end) = match (resources_start, resources_end) {
        (Some((cpu0, rss0)), Some((cpu1, rss1))) => {
            let wall_s = resources_wall_start.elapsed().as_secs_f64();
            let avg = if wall_s > 0.0 {
                Some((cpu1 - cpu0).max(0.0) / wall_s * 100.0)
            } else {
                None
            };
            (avg, Some(rss0), Some(rss1))
        }
        _ => (None, None, None),
    };
    let cpu_pct_max = cpu_samples
        .lock()
        .ok()
        .and_then(|v| v.iter().copied().reduce(f64::max));
    if let (Some(avg), Some(start), Some(end)) = (cpu_pct_avg, rss_bytes_start, rss_bytes_end) {
        eprintln!(
            "session: CPU средний {avg:.1}% ядра (бюджет `PLAN.md` 6.1: < 5%); RSS начало \
             {:.1} МиБ, конец {:.1} МиБ",
            start as f64 / (1024.0 * 1024.0),
            end as f64 / (1024.0 * 1024.0)
        );
    }

    let duration_s = args.minutes.saturating_mul(60);
    let summary = SessionSummary {
        started_utc,
        start_hour_utc,
        duration_s,
        instruments: states.iter().map(|s| s.member.symbol.clone()).collect(),
        records_total,
        gaps,
        clock_samples,
        parse_p99_ns,
        queue_p99_ns,
        cpu_pct_avg,
        cpu_pct_max,
        rss_bytes_start,
        rss_bytes_end,
        out: args.root.clone(),
        debug: is_debug_session(duration_s),
    };
    let record_path = args.root.join("session.json");
    std::fs::write(&record_path, serde_json::to_string_pretty(&summary)?)?;
    Ok(summary)
}

/// Одна книжная запись/сделка -> кадр из одной записи в файл инструмента.
/// Кадр на событие, не батч, — сессия отладочная (`R78`, `≤5 минут`), не
/// продовый рекордер (`commands::record` копит кадр батчем ради байт на
/// запись; здесь простота цикла важнее на этом шаге, см. `CONCERNS`).
fn write_market_event(state: &mut SymbolState, local_ts_ns: i64, payload: crate::bybit::ws::Event) {
    use hftbacktest::types::{LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT};

    // `.clear()` truncates length, keeps capacity — this is the fix for
    // ТУПИК 1 (handoff-04-1): the old code did `let mut records =
    // Vec::new()` here, and while `Vec::new()` itself doesn't allocate, the
    // first `.push` on a zero-capacity `Vec` always does — one allocation
    // per event on the hot path, on every call that pushed at least one
    // record. `tests::million_events_through_replay_feed_allocate_nothing`
    // proves the fix end to end through this exact function, fed by a
    // synthetic `ReplayFeed` over 10⁶ events.
    state.scratch.clear();
    let records = &mut state.scratch;
    match payload {
        crate::bybit::ws::Event::Book(update) => {
            if state.book.apply(&update).is_err() {
                return;
            }
            let exch_ts_ns = update.cts_ms.saturating_mul(1_000_000);
            for (side, levels) in [(Side::Bid, &update.bids), (Side::Ask, &update.asks)] {
                for (price_e9, qty_e9) in levels {
                    records.push(Record {
                        ev: hftbacktest_flags(side, update.is_snapshot),
                        exch_ts_ns,
                        local_ts_ns,
                        price_ticks: price_e9 / state.member.tick_e9,
                        qty_lots: qty_e9 / state.member.step_e9,
                        order_id: 0,
                        ival: 0,
                        fval: 0.0,
                    });
                }
            }
        }
        crate::bybit::ws::Event::Trade(trade) => {
            records.push(Record {
                ev: if trade.aggressor_is_buy {
                    LOCAL_BUY_TRADE_EVENT
                } else {
                    LOCAL_SELL_TRADE_EVENT
                },
                exch_ts_ns: trade.exch_ms.saturating_mul(1_000_000),
                local_ts_ns,
                price_ticks: trade.price_e9 / state.member.tick_e9,
                qty_lots: trade.qty_e9 / state.member.step_e9,
                order_id: 0,
                ival: i64::from(trade.block),
                fval: 0.0,
            });
        }
        crate::bybit::ws::Event::Other => {}
    }
    if state.scratch.is_empty() {
        return;
    }
    if state.writer.write_frame(&state.scratch).is_ok() {
        state.records_written += state.scratch.len() as u64;
    }
}

/// Печатает CPU (% одного ядра — критерий GC "< 5% ядра суммарно",
/// `PLAN.md`, раздел GC) и RSS в stderr раз в 30 с. Фоновый ОС-поток —
/// приём `bybit::verify_sidecar` ("поток откреплён", `JoinHandle` наружу
/// не идёт: остановка вместе с процессом, а не по сигналу); не в горячем
/// пути, туда попадает только раз в 30 с сна и один вызов ОС. Без
/// сторонней зависимости (`sysinfo` не в `Cargo.toml`, добавлять нельзя —
/// `interfaces.md`): то, что уже даёт ОС — `Get-Process` на Windows,
/// `/proc/self/{stat,status}` на Linux.
/// Запускает фоновый сэмплер и возвращает точку, куда он копит каждый
/// посчитанный `%` CPU — `run_session` читает её после цикла, чтобы
/// `cpu_pct_max` в `session.json` был числом, а не только строкой в stderr
/// (таск 20, критерий приёмки 1: «если сэмплер не пишет CPU/RSS в файл —
/// добавь запись»). Поток не присоединяется (как и раньше) — печатает,
/// копит и забывается; `run_session` не ждёт его, только читает `Mutex`
/// один раз в самом конце.
fn spawn_resource_sampler() -> std::sync::Arc<std::sync::Mutex<Vec<f64>>> {
    let pid = std::process::id();
    let samples = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let out = samples.clone();
    std::thread::spawn(move || {
        let mut prev: Option<(std::time::Instant, f64)> = None;
        loop {
            std::thread::sleep(Duration::from_secs(30));
            match sample_resources(pid) {
                Some((cpu_seconds, rss_bytes)) => {
                    let now = std::time::Instant::now();
                    let cpu_line = match prev {
                        Some((prev_at, prev_cpu)) => {
                            let wall_s = now.duration_since(prev_at).as_secs_f64();
                            if wall_s > 0.0 {
                                let pct = (cpu_seconds - prev_cpu).max(0.0) / wall_s * 100.0;
                                if let Ok(mut v) = out.lock() {
                                    v.push(pct);
                                }
                                format!("{pct:.1}% ядра")
                            } else {
                                "н/д (нулевой интервал)".to_string()
                            }
                        }
                        None => "н/д (первый замер)".to_string(),
                    };
                    prev = Some((now, cpu_seconds));
                    eprintln!(
                        "session: ресурсы — CPU {cpu_line}, RSS {:.1} МиБ",
                        rss_bytes as f64 / (1024.0 * 1024.0)
                    );
                }
                None => eprintln!("session: замер CPU/RSS недоступен на этой ОС"),
            }
        }
    });
    samples
}

/// Кумулятивное время CPU в секундах (пользователь+система с момента
/// старта процесса) и RSS в байтах — пара, из которой `spawn_resource_
/// sampler` считает `%` делением дельты первого на дельту секунд между
/// замерами (то самое "измеримое — измеряется", а не готовый процент из
/// стороннего крейта).
#[cfg(target_os = "windows")]
fn sample_resources(pid: u32) -> Option<(f64, u64)> {
    let script = format!(
        "(Get-Process -Id {pid} | Select-Object -Property \
         @{{n='cpu';e={{$_.TotalProcessorTime.TotalSeconds}}}}, \
         @{{n='rss';e={{$_.WorkingSet64}}}} | ConvertTo-Json -Compress)"
    );
    let out = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let v: serde_json::Value = serde_json::from_str(text.trim()).ok()?;
    let cpu = v.get("cpu")?.as_f64()?;
    let rss = v.get("rss")?.as_u64()?;
    Some((cpu, rss))
}

#[cfg(target_os = "linux")]
fn sample_resources(_pid: u32) -> Option<(f64, u64)> {
    // 100 Гц — стандартная частота `USER_HZ` ядра Linux на x86/x86_64
    // (`sysconf(_SC_CLK_TCK)` в подавляющем большинстве сборок); это факт
    // ABI платформы, а не изобретённое число `interfaces.md` — доставать
    // настоящее значение потребовало бы `libc`, которого нет в
    // `Cargo.toml` (закрытый список зависимостей).
    const CLK_TCK: f64 = 100.0;
    let stat = std::fs::read_to_string("/proc/self/stat").ok()?;
    // `comm` (второе поле, в скобках) может содержать пробелы — считаем от
    // последней закрывающей скобки, не от фиксированного индекса.
    let after_comm = stat.rsplit_once(')')?.1;
    let fields: Vec<&str> = after_comm.split_whitespace().collect();
    // Поля `stat(5)` 1-based; после `)` индекс 0 — поле 3 (`state`), значит
    // `utime`/`stime` (поля 14/15) — индексы 11/12 здесь.
    let utime: f64 = fields.get(11)?.parse().ok()?;
    let stime: f64 = fields.get(12)?.parse().ok()?;
    let cpu_seconds = (utime + stime) / CLK_TCK;

    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let rss_kb: u64 = status
        .lines()
        .find_map(|l| l.strip_prefix("VmRSS:"))
        .and_then(|rest| rest.trim().split_whitespace().next())
        .and_then(|kb| kb.parse().ok())?;
    Some((cpu_seconds, rss_kb.saturating_mul(1024)))
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
fn sample_resources(_pid: u32) -> Option<(f64, u64)> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::feed::replay::ReplayFeed;
    use hftbacktest::types::{
        LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
        LOCAL_BUY_TRADE_EVENT,
    };

    const TEST_TICK_E9: i64 = 1_000_000;
    const TEST_STEP_E9: i64 = 1_000_000;

    fn rec(ev: u64, exch_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
        Record {
            ev,
            exch_ts_ns,
            local_ts_ns: exch_ts_ns,
            price_ticks,
            qty_lots,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    /// One resting snapshot (bid + ask, never touched again) followed by
    /// `pairs` repetitions of (bid-depth delta, trade) — same shape as the
    /// `record.rs::steady_events_allocate_nothing` precedent ("форма и
    /// magnitudes не меняются, меняются только монотонные счётчики"), built
    /// as a real binlog so the test drives `write_market_event` through
    /// `ReplayFeed`, not by calling it directly — that is the shape ТУПИК 1
    /// (handoff-04-1) named: "синтетический `ReplayFeed`", not a synthetic
    /// `Event` fed straight to the function under test.
    fn build_synthetic_binlog(pairs: usize) -> Vec<u8> {
        const PER_FRAME: usize = 20_000;
        // `max_records_per_frame` sizes the reader's decompression-bomb
        // ceiling as `declared_max * MIN_RECORD_LEN` (`binlog::mod::
        // max_frame_payload_bytes`) — a ceiling computed from records at
        // their *minimum* varint size, not their real one. Declaring it
        // equal to `PER_FRAME` made every chunk here reject once real
        // (non-zero-delta) encoding pushed a 20,000-record frame past that
        // tight ceiling (found by bisection: broke between 2,000 and 5,000
        // pairs). `binlog::tests::round_trips_one_million_events` sets this
        // field to 1,000,000 while writing the very same 20,000-record
        // chunks — the same headroom, reused here rather than reinvented.
        let header = Header {
            tick_e9: TEST_TICK_E9,
            step_e9: TEST_STEP_E9,
            max_records_per_frame: 1_000_000,
        };
        let mut w = Writer::create(Vec::new(), header, 0).unwrap();
        w.write_frame(&[
            rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 5),
            rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, 0, 110, 5),
        ])
        .unwrap();

        let mut buf: Vec<Record> = Vec::with_capacity(PER_FRAME);
        for k in 0..pairs {
            let ts = 1_000_000 + (2 * k as i64 + 1) * 1_000;
            buf.push(rec(LOCAL_BID_DEPTH_EVENT, ts, 100, 5));
            buf.push(rec(LOCAL_BUY_TRADE_EVENT, ts + 1_000, 100, 1));
            if buf.len() >= PER_FRAME {
                w.write_frame(&buf).unwrap();
                buf.clear();
            }
        }
        if !buf.is_empty() {
            w.write_frame(&buf).unwrap();
        }
        w.into_inner()
    }

    fn fresh_state(root: &Path) -> SymbolState {
        let path = root.join("SYM.binlog");
        let file = std::fs::File::create(&path).unwrap();
        let header = Header {
            tick_e9: TEST_TICK_E9,
            step_e9: TEST_STEP_E9,
            max_records_per_frame: crate::commands::record::MAX_RECORDS_PER_FRAME,
        };
        let writer = Writer::create(file, header, ZSTD_LEVEL).unwrap();
        SymbolState {
            member: PoolMember {
                symbol: "SYM".to_string(),
                tick_e9: TEST_TICK_E9,
                step_e9: TEST_STEP_E9,
            },
            book: Book::new(TEST_TICK_E9, TEST_STEP_E9),
            writer,
            records_written: 0,
            scratch: Vec::with_capacity(128),
        }
    }

    /// ТУПИК 1 (handoff-04-1), closed: `write_market_event` used to build a
    /// fresh `Vec<Record>` every call, which allocates on its first `push` —
    /// one allocation per event on the hot path. Drives `state.scratch`
    /// through `ReplayFeed` over 10⁶ synthetic events and sums
    /// `alloc_count::measure` **around each `write_market_event` call
    /// alone** (after a warmup that lets `Writer`'s internal buffers and
    /// `state.scratch` reach steady-state capacity), not around
    /// `feed.next_event()` — `ReplayFeed`'s own `FileReplayer`
    /// (`bybit::verify.rs`, outside this task's zone) rebuilds its
    /// `Update.bids`/`.asks` with `std::mem::take` on every flush, which
    /// resets those `Vec`s to zero capacity and reallocates them on the next
    /// push; that is a real, separate allocation source this test does not
    /// claim to have fixed. What this test proves is exactly what ТУПИК 1
    /// named: the function under test, not its synthetic data source.
    #[test]
    fn million_events_through_replay_feed_allocate_nothing_after_warmup() {
        const WARMUP_EVENTS: usize = 2_000;
        const MEASURED_EVENTS: usize = 1_000_000;
        // +1 snapshot event per 2 records (bid delta + trade); generous
        // margin so the feed never runs dry before `MEASURED_EVENTS` is hit.
        let pairs = (WARMUP_EVENTS + MEASURED_EVENTS) / 2 + 10;
        let bytes = build_synthetic_binlog(pairs);
        let mut feed = ReplayFeed::open(0, &bytes[..]).unwrap();

        let dir = tempfile::tempdir().unwrap();
        let mut state = fresh_state(dir.path());

        let mut next_market_event = || loop {
            match feed
                .next_event()
                .expect("синтетический ReplayFeed обязан покрыть весь замер")
            {
                Event::Market {
                    local_ts_ns,
                    payload,
                    ..
                } => return (local_ts_ns, payload),
                Event::Gap { .. } => continue,
            }
        };

        for _ in 0..WARMUP_EVENTS {
            let (local_ts_ns, payload) = next_market_event();
            write_market_event(&mut state, local_ts_ns, payload);
        }

        let mut measured_allocations = 0u64;
        for _ in 0..MEASURED_EVENTS {
            let (local_ts_ns, payload) = next_market_event();
            let (_, counts) = crate::alloc_count::measure(|| {
                write_market_event(&mut state, local_ts_ns, payload)
            });
            measured_allocations += counts.allocations;
        }
        assert_eq!(
            measured_allocations, 0,
            "write_market_event обязана не аллоцировать после прогрева на 10^6 событий"
        );
    }

    fn args_with_minutes(minutes: u64) -> SessionArgs {
        SessionArgs {
            pool_instruments: PathBuf::from("does/not/exist/instruments.csv"),
            root: PathBuf::from("does/not/exist/root"),
            minutes,
            base_url: BYBIT_MAINNET_URL.to_string(),
            ntp_addr: "pool.ntp.org:123".to_string(),
        }
    }

    /// История 7 / R38 дословно: «от пяти до пятнадцати минут». Значение вне
    /// `MIN_MINUTES..=MAX_MINUTES` обязано провалиться на самой первой строке
    /// `run_session` — до `create_dir_all`/`load_pool` — иначе сообщение об
    /// ошибке пришло бы от отсутствующего `instruments.csv`, а не от границы
    /// `--minutes`, и звало бы сеть/диск раньше проверки диапазона.
    #[test]
    fn run_session_rejects_minutes_outside_five_to_fifteen_before_touching_disk() {
        for bad in [0, MIN_MINUTES - 1, MAX_MINUTES + 1, 60] {
            let err =
                run_session(&args_with_minutes(bad)).expect_err("вне 5..=15 обязана быть ошибка");
            let msg = err.to_string();
            assert!(
                msg.contains("--minutes") && msg.contains(&bad.to_string()),
                "сообщение обязано называть флаг и полученное значение {bad}: {msg}"
            );
        }
    }

    /// Граничные значения (5 и 15) обязаны пройти проверку диапазона и
    /// провалиться дальше, на отсутствующем `instruments.csv` — не на
    /// сообщении про `--minutes`. Так тест ловит и «отклоняет legit
    /// значения», и «ошибка не о границе» одним прогоном.
    #[test]
    fn run_session_accepts_boundary_minutes_and_proceeds_past_validation() {
        for ok in [MIN_MINUTES, MAX_MINUTES] {
            let err = run_session(&args_with_minutes(ok)).expect_err("несуществующий пул — ошибка");
            let msg = err.to_string();
            assert!(
                !msg.contains("--minutes"),
                "{ok} — валидная граница, ошибка обязана быть не про --minutes: {msg}"
            );
        }
    }

    /// `session.json.debug` — чистая функция от длительности (таск 17,
    /// пункт 5б): `true`, пока сессия короче часа (`CLAUDE.md`: тестовый
    /// прогон — фаза отладки, результат не данные), `false` начиная ровно с
    /// часа. `MAX_MINUTES = 15` не даёт этой ветке случиться через сегодняшний
    /// CLI — тест бьёт по самой функции, не по `run_session`.
    #[test]
    fn is_debug_session_true_under_an_hour_false_at_and_past_it() {
        assert!(is_debug_session(0));
        assert!(is_debug_session(3_599));
        assert!(!is_debug_session(3_600));
        assert!(!is_debug_session(3_601));
    }

    // -----------------------------------------------------------------
    // Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
    // `<SYMBOL>.binlog` — то имя, которое читают `verify`/`levels`/
    // `markout` (слепая приёмка G4).
    // -----------------------------------------------------------------

    /// Ожидаемое имя — независимый разбор `1_757_800_000_000_000_000` как
    /// `2025-09-13`, взятый из уже существующего оракула
    /// `commands::record::tests::day_string_of_ns_uses_utc_not_the_hosts_timezone`
    /// (не пересчитан этим тестом заново): `session_binlog_path` обязана
    /// давать ровно ту же дату и то же имя, что `day_file_path(.., 1)`.
    #[test]
    fn session_binlog_path_carries_the_utc_start_day() {
        let root = PathBuf::from("data/session-debug");
        let path = session_binlog_path(&root, "SOLUSDT", 1_757_800_000_000_000_000).unwrap();
        assert_eq!(
            path,
            crate::commands::record::day_file_path(&root, "SOLUSDT", "2025-09-13", 1)
        );
        assert_eq!(
            path.file_name().and_then(|n| n.to_str()),
            Some("SOLUSDT-2025-09-13.binlog")
        );
    }

    /// Слепая приёмка G4: каталог, размеченный так, как `run_session`
    /// раскладывает файлы начиная с этого таска (`<SYMBOL>-<день>.binlog`,
    /// день часть 1 — та же `day_file_path`, что доказал предыдущий тест),
    /// читается `verify`/`levels` без ручного переименования. Фикстура
    /// собрана публичной `test_support::write_day` (тот же шов, что
    /// `pilot.rs::process_instrument` уже использует для этой пары команд),
    /// не вызовом `run_session` целиком — та сама открывает `LiveFeed`
    /// (живой сокет), офлайн-тестам сеть недоступна (`CLAUDE.md`).
    #[test]
    fn directory_named_like_a_fresh_session_reads_through_verify_and_levels_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let day = "2026-09-08";

        super::super::test_support::write_day(
            root,
            "SOLUSDT",
            day,
            &super::super::test_support::three_level_frames(),
        );
        std::fs::write(
            crate::commands::record::instruments_csv_path(root),
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
             SOLUSDT,0.01,0.1,0.1,5,5\n",
        )
        .unwrap();

        let vs = crate::bybit::verify::run_verify(&crate::bybit::verify::VerifyArgs {
            symbol: "SOLUSDT".to_string(),
            root: root.to_path_buf(),
        })
        .expect("verify обязан найти суточный файл сессии без переименования");
        assert_eq!(vs.files, 1, "ровно один суточный файл сессии");

        let ls = super::super::levels::run_levels(&super::super::levels::LevelsArgs {
            root: root.to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3: super::super::H3Args {
                h3_mode: super::super::H3ModeArg::Floor,
                h3_lots: None,
            },
            h3_k: None,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            out: Some(root.join("levels-SOLUSDT.csv")),
        })
        .expect("levels обязан найти суточный файл сессии без переименования");
        assert!(
            ls.levels > 0,
            "фикстура three_level_frames обязана дать хотя бы один уровень"
        );
    }
}
