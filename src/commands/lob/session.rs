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

use std::fs::File;
use std::io::{Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use clap::Args;

use crate::binlog::{Record, Writer};
use crate::book::{Book, Side};
use crate::bybit::clock::{append_row, sample, BybitServerTimeSource, ClockRow, UdpNtpSource};
use crate::bybit::conn::{Clock, SystemClock};
use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::commands::record::{
    append_gap_row, claim_part_with, day_string_of_ns, ensure_gaps_csv, event_exch_ms,
    gaps_csv_path, ts_utc_of_ns, GapKind, GapRow, FRAME_LOSS_WINDOW_SECS, FRAME_TARGET_RECORDS,
    HOURLY_REFRESH_SECS, NS_PER_DAY,
};
use crate::feed::live::{LiveFeed, PoolMember};
use crate::feed::{Event, Feed, GapKind as FeedGapKind};

/// Граница `--minutes` — брифу владельца дословно: «данные набираются
/// короткими сессиями — от пяти до пятнадцати минут по всему пулу
/// одновременно» (история 7, `R38`, `docs/plan/BUSINESS-TASK.md`). Не
/// умолчание и не изобретённое число этого файла — предел приходит из
/// текста задачи, не из кода.
pub const MIN_MINUTES: u64 = 5;
pub const MAX_MINUTES: u64 = 15;

/// Границы `--pilot-minutes` (таск 22): пилот §11 плана — режим вне лимита
/// сессии сбора, поэтому отдельный флаг, а не число вне диапазона
/// `--minutes`. Нижняя граница — просто «дольше, чем сессия сбора»
/// (`MAX_MINUTES + 1`, не второе изобретённое число); верхняя — гейт G0
/// плана («у медианного инструмента < 200 уровней — пилот продлевается до
/// 6 часов один раз», ревизия 17б, `docs/plan/SETTLED.md` В-29). Сама длина
/// пилота — решение владельца по факту (2026-09-12: 30 минут, не два часа
/// первой редакции §11) и в код не зашита, только эти границы.
pub const MIN_PILOT_MINUTES: u32 = MAX_MINUTES as u32 + 1;
pub const MAX_PILOT_MINUTES: u32 = 360;

/// Аргументы `lob session`. Ни у `minutes`/`pilot_minutes`, ни у путей нет
/// правдоподобного умолчания (правило 1 `interfaces.md`: параметр без
/// умолчания лучше изобретённого) — пул и место записи владелец называет
/// каждый раз.
#[derive(Debug, Args)]
pub struct SessionArgs {
    /// `instruments.csv` последнего `lob pick` — колонки `symbol,tick_size,
    /// min_order_qty,qty_step,min_notional_value` (`CLAUDE.md`, «грабли»).
    /// Взаимоисключающий с `--all-instruments`; ровно один обязателен.
    #[arg(long, conflicts_with = "all_instruments")]
    pub pool_instruments: Option<PathBuf>,
    /// Писать **все** linear USDT-перпетуалы биржи, без отбора (таск 28,
    /// R86: «писать не 10 монет а например 500 также эффективно и
    /// экономно»). Список — `instruments-info` тем же путём, что у `lob
    /// pick` (`bybit::rest::fetch_all_linear_instruments`), фильтр — только
    /// «linear USDT-перпетуал в торгах»: исключения §2 (пул анализа) здесь
    /// **не применяются** — это запись, а не отбор. `tick_e9`/`step_e9` —
    /// из того же ответа, не из CSV.
    #[arg(long, conflicts_with = "pool_instruments")]
    pub all_instruments: bool,
    /// Корень сессии: по файлу `<SYMBOL>-<день UTC старта>.binlog` на
    /// инструмент (то же имя, что читают `verify`/`levels`/`markout` —
    /// `commands::record::day_file_path`), `gaps.csv`, `clock.csv`, запись
    /// о сессии.
    #[arg(long)]
    pub root: PathBuf,
    /// Длина сессии сбора. Диапазон `MIN_MINUTES..=MAX_MINUTES` — решение
    /// владельца (история 7), не умолчание этого файла; `resolve_duration`
    /// отклоняет значения вне него до всякой сети. Взаимоисключающий с
    /// `--pilot-minutes`/`--always-on` (`clap conflicts_with`); ровно один
    /// из трёх обязателен — эту половину условия clap не выражает, её
    /// проверяет `resolve_duration`.
    #[arg(long, conflicts_with_all = ["pilot_minutes", "always_on"])]
    pub minutes: Option<u64>,
    /// Длина пилота §11 плана, минуты — режим вне лимита сессии сбора
    /// (`MIN_PILOT_MINUTES..=MAX_PILOT_MINUTES`, doc констант). Пилот
    /// печатает `pilot: <n> мин` в stderr и несёт `session.json.pilot =
    /// true`, `pilot_minutes = <n>` — всё остальное (пул, `gaps.csv`,
    /// `clock.csv`, CPU/RSS, p99 разбора/очереди) как у обычной сессии.
    #[arg(long, conflicts_with_all = ["minutes", "always_on"])]
    pub pilot_minutes: Option<u32>,
    /// Олвейс-он коллектор (таск 25, В-34): без дедлайна, до Ctrl+C;
    /// новые сутки UTC — новая часть файла на инструмент; `session.json` и
    /// `clock.csv` переписываются раз в час (`record::HOURLY_REFRESH_SECS`)
    /// и на остановке. `session.json.always_on = true`. Сам по себе ничего
    /// не ограничивает по времени — правило владельца «не дольше 5 минут до
    /// вердикта экономии» соблюдает оператор (`timeout`/Ctrl+C).
    #[arg(long, conflicts_with_all = ["minutes", "pilot_minutes"])]
    pub always_on: bool,
    /// REST-хост Bybit v5 для одного замера `serverTime` в `clock.csv`.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// NTP-эталон для того же замера. Публичный пул `pool.ntp.org` — то же
    /// эксплуатационное решение хоста, что уже подразумевает `ASSUMPTION
    /// H12` («часы дисциплинируются NTP»), без выбора конкретного адреса.
    #[arg(long, default_value = "pool.ntp.org:123")]
    pub ntp_addr: String,
}

/// Режим прогона, разрешённый из трёх взаимоисключающих флагов.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionPlan {
    /// `--minutes` (сессия сбора) или `--pilot-minutes` (пилот §11):
    /// дедлайн через `minutes`.
    Timed {
        minutes: u64,
        pilot_minutes: Option<u32>,
    },
    /// `--always-on`: без дедлайна, до `None` от `Feed` (Ctrl+C).
    AlwaysOn,
}

/// Ровно один из `--minutes`/`--pilot-minutes`/`--always-on` — `clap
/// conflicts_with` запрещает любые два разом, но не делает ни один
/// обязательным сам по себе; эта функция закрывает вторую половину условия
/// и проверяет диапазон до сети и диска (тот же приём, что раньше был
/// инлайн-проверкой `--minutes` в начале `run_session`).
fn resolve_duration(args: &SessionArgs) -> anyhow::Result<SessionPlan> {
    match (args.minutes, args.pilot_minutes, args.always_on) {
        (Some(minutes), None, false) => {
            if !(MIN_MINUTES..=MAX_MINUTES).contains(&minutes) {
                anyhow::bail!(
                    "--minutes обязан быть в {MIN_MINUTES}..={MAX_MINUTES} (история 7, R38: «от \
                     пяти до пятнадцати минут»): получено {minutes}"
                );
            }
            Ok(SessionPlan::Timed {
                minutes,
                pilot_minutes: None,
            })
        }
        (None, Some(pilot_minutes), false) => {
            if !(MIN_PILOT_MINUTES..=MAX_PILOT_MINUTES).contains(&pilot_minutes) {
                anyhow::bail!(
                    "--pilot-minutes обязан быть в {MIN_PILOT_MINUTES}..={MAX_PILOT_MINUTES} \
                     (PLAN.md §11, гейт G0: «продление до 6 часов один раз»): получено \
                     {pilot_minutes}"
                );
            }
            Ok(SessionPlan::Timed {
                minutes: u64::from(pilot_minutes),
                pilot_minutes: Some(pilot_minutes),
            })
        }
        (None, None, true) => Ok(SessionPlan::AlwaysOn),
        (None, None, false) => anyhow::bail!(
            "нужен ровно один флаг: --minutes <{MIN_MINUTES}..={MAX_MINUTES}> для сессии сбора, \
             --pilot-minutes <{MIN_PILOT_MINUTES}..={MAX_PILOT_MINUTES}> для пилота §11 или \
             --always-on для олвейс-он коллектора (В-34)"
        ),
        _ => unreachable!(
            "clap conflicts_with запрещает --minutes/--pilot-minutes/--always-on разом"
        ),
    }
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

/// Пул сессии: либо готовый `instruments.csv` (`--pool-instruments`), либо
/// весь список биржи (`--all-instruments`, таск 28). Ровно один — вторую
/// половину условия `clap conflicts_with` не выражает, как и у
/// `--minutes`/`--pilot-minutes`/`--always-on`.
fn resolve_pool(args: &SessionArgs) -> anyhow::Result<Vec<PoolMember>> {
    match (&args.pool_instruments, args.all_instruments) {
        (Some(path), false) => load_pool(path),
        (None, true) => load_pool_all(&args.base_url),
        (None, false) => anyhow::bail!(
            "нужен ровно один флаг пула: --pool-instruments <instruments.csv> (пул `lob pick`) \
             или --all-instruments (все linear USDT-перпетуалы биржи, R86)"
        ),
        // Не `unreachable!`: `run_session` зовут и программно
        // (`pilot.rs`), минуя разбор `clap`, и тогда `conflicts_with`
        // ничего не гарантирует — паника вместо ошибки уронила бы пилот.
        (Some(_), true) => anyhow::bail!(
            "--pool-instruments и --all-instruments взаимоисключающие: задан ровно один"
        ),
    }
}

/// Признаки linear USDT-перпетуала в торгах — те же три поля
/// `instruments-info`, по которым отбирает `pick::pool` (`quote_coin`,
/// `contract_type`, `status`). Здесь они стоят отдельно нарочно: пул
/// анализа применяет сверх них исключения §2 (не-крипто база, молодые
/// контракты, дедуп), запись — нет.
const LINEAR_QUOTE_COIN: &str = "USDT";
const LINEAR_CONTRACT_TYPE: &str = "LinearPerpetual";
const TRADING_STATUS: &str = "Trading";

fn load_pool_all(base_url: &str) -> anyhow::Result<Vec<PoolMember>> {
    let mut client = crate::bybit::rest::BybitPublicRest::new(base_url)
        .map_err(|e| anyhow::anyhow!("instruments-info: {e:?}"))?;
    let all = crate::bybit::rest::fetch_all_linear_instruments(&mut client)
        .map_err(|e| anyhow::anyhow!("instruments-info: {e:?}"))?;
    let considered = all.len();
    let pool: Vec<PoolMember> = all
        .into_iter()
        .filter(|i| {
            i.quote_coin == LINEAR_QUOTE_COIN
                && i.contract_type == LINEAR_CONTRACT_TYPE
                && i.status == TRADING_STATUS
        })
        .map(|i| PoolMember {
            symbol: i.symbol,
            tick_e9: i.tick_e9,
            step_e9: i.qty_step_e9,
        })
        .collect();
    if pool.is_empty() {
        anyhow::bail!("instruments-info вернул {considered} инструментов, ни один не linear USDT-перпетуал в торгах");
    }
    eprintln!(
        "session: --all-instruments — {} из {considered} записей instruments-info \
         (linear/{LINEAR_QUOTE_COIN}/{LINEAR_CONTRACT_TYPE}/{TRADING_STATUS}, без исключений §2)",
        pool.len()
    );
    Ok(pool)
}

/// Приёмник файла части (таск 25): кадр целиком копится здесь и уходит на
/// диск **одним** `write_all` по `flush` — файл на диске всегда кончается
/// на границе кадра (кроме краха посреди самого системного вызова), и
/// анализ по накопленному (`verify`/`levels`/`markout` читают живой
/// каталог, а их `Reader` на обрезанном кадре отдаёт `ShortRead`, не
/// «до последнего полного») видит только целые кадры. `BufWriter` таска 24
/// этого не давал: его буфер переполнялся посреди кадра и оставлял на диске
/// длину без тела до следующего сброса. Цена та же — один системный вызов
/// на кадр; ёмкость буфера — по факту первых кадров, дальше не растёт.
pub(crate) struct FrameSink {
    file: Box<dyn SinkFile>,
    buf: Vec<u8>,
    bytes_written: u64,
    /// Запись упала **и** откат к границе кадра не удался: с этого места
    /// файл нечитаем (`Reader` отдаст `ShortRead`), приёмник больше ничего
    /// не пишет — вызывающий закрывает часть и берёт следующую
    /// (`SessionCtx::flush_symbol`).
    boundary_lost: bool,
}

/// Файл под `FrameSink`: `File` в бою, двойник с падающей записью в
/// тестах. Сверх `Write` — одна операция: вернуть файл на границу
/// последнего целого кадра после неудавшейся записи.
pub(crate) trait SinkFile: std::io::Write {
    /// Обрезать файл до `len` байт и поставить курсор на этот конец
    /// (`set_len` один курсор не двигает: следующая запись за старым EOF
    /// дописала бы нули).
    fn truncate_to(&mut self, len: u64) -> std::io::Result<()>;
}

impl SinkFile for File {
    fn truncate_to(&mut self, len: u64) -> std::io::Result<()> {
        self.set_len(len)?;
        self.seek(SeekFrom::Start(len)).map(|_| ())
    }
}

/// Самый крупный кадр этой сессии: порог батча плюс самое крупное
/// сообщение (снапшот 50+50 — 128 с запасом, см. `SymbolState::scratch`) —
/// то же слагаемое, что у ёмкости `batch`; синтетический снапшот ротации
/// (≤ 256 записей) заведомо меньше.
const SESSION_MAX_FRAME_RECORDS: usize = FRAME_TARGET_RECORDS + 128;

impl FrameSink {
    pub(crate) fn new(file: File) -> Self {
        Self::over(Box::new(file))
    }

    /// Приёмник над любым `SinkFile` — шов для теста с падающей записью.
    pub(crate) fn over(file: Box<dyn SinkFile>) -> Self {
        Self {
            file,
            // Резерв по верхней границе формата один раз — иначе первый же
            // кадр крупнее всех предыдущих перевыделял бы буфер посреди
            // прогона (гейт «ноль аллокаций после прогрева»).
            buf: Vec::with_capacity(crate::binlog::max_frame_bytes_on_disk(
                SESSION_MAX_FRAME_RECORDS,
            )),
            bytes_written: 0,
            boundary_lost: false,
        }
    }

    /// Байт ушло на диск через этот приёмник (заголовок включительно).
    pub(crate) fn bytes_written(&self) -> u64 {
        self.bytes_written
    }

    /// См. поле `boundary_lost`.
    pub(crate) fn boundary_lost(&self) -> bool {
        self.boundary_lost
    }
}

impl std::io::Write for FrameSink {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.buf.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    /// Кадр — одним `write_all`. Ошибка: буфер пустеет в любом исходе
    /// (вызывающий уже считает кадр потерянным, повтор смешал бы порядок),
    /// а файл откатывается на `bytes_written` — границу последнего целого
    /// кадра: частичная запись оставила бы на диске обрывок, и следующий
    /// кадр, дописанный следом, сделал бы часть нечитаемой с этого места
    /// (`ShortRead`). Если и откат не удался — `boundary_lost`, дальше
    /// приёмник ничего не пишет.
    fn flush(&mut self) -> std::io::Result<()> {
        if self.boundary_lost {
            self.buf.clear();
            return Err(std::io::Error::other(
                "граница кадра потеряна — часть закрыта, нужна следующая",
            ));
        }
        if self.buf.is_empty() {
            return self.file.flush();
        }
        let len = self.buf.len() as u64;
        let written = self
            .file
            .write_all(&self.buf)
            .and_then(|()| self.file.flush());
        self.buf.clear();
        match written {
            Ok(()) => {
                self.bytes_written += len;
                Ok(())
            }
            Err(e) => match self.file.truncate_to(self.bytes_written) {
                Ok(()) => Err(e),
                Err(t) => {
                    self.boundary_lost = true;
                    Err(std::io::Error::new(
                        e.kind(),
                        format!("{e}; откат к границе кадра не удался: {t}"),
                    ))
                }
            },
        }
    }
}

/// Состояние одного инструмента пула: своя книга, свой файл. Индекс в этом
/// `Vec` — тот же `symbol: u16`, которым `Feed` метит каждое событие
/// (`interfaces.md`: тег события — не строка, лукап по строке на каждое
/// событие был бы `HashMap` на пути события, запрет 7).
struct SymbolState {
    member: PoolMember,
    writer: Writer<FrameSink>,
    /// Номер части и индекс суток текущего файла (`ts / NS_PER_DAY`, как
    /// `record::Recorder::day_index` — целочисленное деление на событие,
    /// строка даты только на ротации).
    part: u32,
    day_index: i64,
    /// Книга инструмента — ради синтетического снапшота первым кадром
    /// новых суток (таск 25, как `record::Recorder::ensure_day` +
    /// `on_snapshot`): без него файл суток начинался бы с дельт, и
    /// `verify`/`levels` отбросили бы сутки целиком. Таск 24 снял вторую
    /// книгу как лишнюю проверку — здесь она не проверка, а источник
    /// первого кадра; `apply` на дельту — ноль аллокаций (гейт таска 24).
    book: Book,
    /// Книга доверена с последнего снапшота биржи (сброс на любом `Gap`
    /// ресинка/переподключения).
    synced: bool,
    /// В текущем файле уже лежит первый кадр-снапшот; до него дельты и
    /// сделки в файл не идут (`record::RecordError::NoSnapshot`, тот же
    /// контракт) — иначе сутки нечитаемы.
    has_snapshot: bool,
    records_written: u64,
    /// Ротация суток не удалась (`claim_part_with`): следующая попытка не
    /// раньше этой метки — окно `FRAME_LOSS_WINDOW_SECS`, чтобы отказ диска
    /// не давал строку `gaps.csv` на каждое событие; до неё события новых
    /// суток идут в текущую часть.
    rotate_retry_after_ns: i64,
    /// Кадров, которые не записались (таск 25): каждый — строка `gaps.csv`
    /// `write_failed`, квант потери — до `FRAME_TARGET_RECORDS` записей.
    frames_failed: u64,
    /// Скретч-буфер `write_market_event` — переиспользуется на каждое
    /// событие вместо `Vec::new()`, иначе горячий путь аллоцирует ровно там,
    /// где гейт GC требует ноль (`interfaces.md`, запрет 1; было ТУПИКОМ 1
    /// таска 04). Ёмкость с запасом на самый крупный кадр этого потока —
    /// `orderbook.50` снапшот обеих сторон, 50+50 записей; `.clear()` в
    /// начале `write_market_event` не освобождает ёмкость, только длину.
    scratch: Vec<Record>,
    /// Накопитель кадра (таск 24, критерий «батчинг как в `lob record`»):
    /// `write_market_event` переносит `scratch` сюда через `Vec::append`
    /// (перемещение элементов, не копия — `scratch` пустеет, ёмкость цела) и
    /// пишет кадр `binlog::Writer`, только когда здесь накопилось
    /// `FRAME_TARGET_RECORDS` записей или пришёл тик (`Event::Tick`, окно
    /// `FRAME_LOSS_WINDOW_SECS`). Раньше здесь писался кадр на **каждое**
    /// сообщение — `docs/findings/collector-2026-09-12.md`, «Замер до»:
    /// кадр из 1–5 записей почти не сжимается. Ёмкость с запасом на самое
    /// крупное сообщение (128 записей, см. `scratch`) сверх порога — чтобы
    /// приход этого сообщения ровно на границе порога не вызвал
    /// перевыделение до `flush_symbol_batch`.
    batch: Vec<Record>,
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
}

/// Отладочная сессия — короче часа. Чистая функция от `duration_s`, а не
/// прямая проверка `args.minutes < 60` внутри `run_session`: так у неё есть
/// собственный тест на обе ветки (`< 3600` и `>= 3600`).
fn is_debug_session(duration_s: u64) -> bool {
    const HOUR_S: u64 = 3600;
    duration_s < HOUR_S
}

/// Дописывает один замер в `clock.csv` — best-effort: провал замера не
/// роняет сессию, только не даёт строки. Сеть (NTP, REST) — **не** в
/// событийном цикле (A9, запрет 4): зовётся из `spawn_clock_sampler` (свой
/// ОС-поток, раз в `HOURLY_REFRESH_SECS`) и один раз после цикла.
fn take_clock_sample(ntp_addr: &str, base_url: &str, idx: u64, clock_csv: &Path) -> bool {
    let mut ntp = match UdpNtpSource::connect(ntp_addr, Duration::from_secs(2)) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("session: clock — NTP {ntp_addr} недоступен: {e:?}");
            return false;
        }
    };
    let mut bybit = match BybitServerTimeSource::new(base_url.to_string()) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("session: clock — Bybit serverTime недоступен: {e:?}");
            return false;
        }
    };
    let row: ClockRow = sample(idx, &SystemClock, &mut ntp, &mut bybit);
    match append_row(clock_csv, &row) {
        Ok(()) => true,
        Err(e) => {
            eprintln!("session: clock.csv не дописался: {e:?}");
            false
        }
    }
}

/// Часовой замер `clock.csv` в своём ОС-потоке (таск 25): первый — сразу,
/// дальше раз в `HOURLY_REFRESH_SECS` (тот же часовой таймер, что у
/// `lob record` — «`clock.csv` (шаг 0.5)», doc константы). Счётчик удачных
/// строк — наружу атомиком; поток не присоединяется, живёт до конца
/// процесса, как сэмплер ресурсов.
fn spawn_clock_sampler(
    ntp_addr: String,
    base_url: String,
    clock_csv: PathBuf,
    count: Arc<AtomicU64>,
) {
    std::thread::spawn(move || loop {
        let idx = count.load(Ordering::Relaxed);
        if take_clock_sample(&ntp_addr, &base_url, idx, &clock_csv) {
            count.fetch_add(1, Ordering::Relaxed);
        }
        std::thread::sleep(Duration::from_secs(HOURLY_REFRESH_SECS));
    });
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
        let deadline_ns = match plan {
            SessionPlan::Timed { minutes, .. } => Some(
                started_ns
                    + i64::try_from(minutes.saturating_mul(60)).unwrap_or(i64::MAX) * 1_000_000_000,
            ),
            SessionPlan::AlwaysOn => None,
        };
        let resources_pid = std::process::id();
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
        })
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
fn run_session_loop(feed: &mut dyn Feed, ctx: &mut SessionCtx) -> anyhow::Result<SessionSummary> {
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
            Event::Tick { local_ts_ns } => ctx.on_tick(local_ts_ns),
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

/// Синтетический полный снапшот из книги инструмента первым кадром новых
/// суток (таск 25, Decision 7 / `record::Recorder::on_snapshot`): по
/// записи на уровень обеих сторон, флаги снапшота.
fn push_book_snapshot(state: &mut SymbolState, exch_ts_ns: i64, local_ts_ns: i64) {
    state.batch.clear();
    for side in [Side::Bid, Side::Ask] {
        for (tick, lots) in state.book.levels(side) {
            state.batch.push(Record {
                ev: hftbacktest_flags(side, true),
                exch_ts_ns,
                local_ts_ns,
                price_ticks: tick,
                qty_lots: lots,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            });
        }
    }
    state.has_snapshot = true;
}

/// Одна книжная запись/сделка → в накопитель кадра инструмента
/// (`SymbolState::batch`); кадр на диск — `flush_symbol_batch` по порогу
/// `FRAME_TARGET_RECORDS` или сразу на снапшоте (как `Recorder::
/// on_snapshot`: первый кадр файла не ждёт тика); третий повод — тик
/// (`SessionCtx::on_tick`). Раньше эта функция сама писала `binlog::
/// Writer::write_frame` на **каждое** сообщение — `docs/findings/
/// collector-2026-09-12.md`, «Замер до». `Err` — кадр не записался
/// (счётчик `frames_failed` уже увеличен, батч очищен).
///
/// Книга инструмента (`state.book`) ведётся ради синтетического снапшота
/// на ротации суток (таск 25), не как вторая проверка: `bybit::conn::
/// handle_raw` уже применил то же обновление к своей книге и не пересылает
/// ничего, что не прошло `apply`, поэтому `apply` здесь на той же
/// последовательности не может дать другого исхода (таск 24) — а если даёт
/// (`Err`), это дефект, и книга помечается недоверенной, не молчит.
/// До первого снапшота в файле дельты и сделки не пишутся (как `record::
/// RecordError::NoSnapshot`): файл, начатый с дельт, нечитаем целиком.
/// Ни одно поле `Record` не читает состояние книги: цена/размер строятся
/// из самого `update`/`trade`.
fn write_market_event(
    state: &mut SymbolState,
    local_ts_ns: i64,
    payload: crate::bybit::ws::Event,
) -> Result<(), crate::binlog::BinlogError> {
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
    let mut is_snapshot = false;
    match payload {
        crate::bybit::ws::Event::Book(update) => {
            match state.book.apply(&update) {
                Ok(()) => {
                    if update.is_snapshot {
                        state.synced = true;
                    }
                }
                Err(_) => {
                    state.synced = false;
                }
            }
            if update.is_snapshot {
                // Снапшот биржи открывает файл (или продолжает его после
                // ресинка) — своим кадром, сразу: незакрытый батч дельт
                // уходит кадром первым, как в `Recorder::on_snapshot`.
                is_snapshot = true;
                state.has_snapshot = true;
            } else if !state.has_snapshot {
                return Ok(());
            }
            let exch_ts_ns = update.cts_ms.saturating_mul(1_000_000);
            let records = &mut state.scratch;
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
            if !state.has_snapshot {
                return Ok(());
            }
            state.scratch.push(Record {
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
        return Ok(());
    }
    // Перемещение элементов из `scratch` в `batch` (не копия): `scratch`
    // остаётся с прежней ёмкостью и нулевой длиной, `batch` растёт до
    // порога, а не пишется кадром сразу.
    state.batch.append(&mut state.scratch);
    if is_snapshot || state.batch.len() >= FRAME_TARGET_RECORDS {
        flush_symbol_batch(state)?;
    }
    Ok(())
}

/// Пишет накопленный `batch` одним кадром `binlog::Writer` и сразу
/// сбрасывает приёмник (`FrameSink::flush` — один `write_all` на кадр:
/// файл на диске кончается на границе кадра), опустошает батч (`.clear()`
/// — длина в ноль, ёмкость цела). Пустой батч — no-op, тот же контракт, что
/// `binlog::Writer::write_frame`. Ошибка — наружу: вызывающий считает
/// `frames_failed` и пишет строку `gaps.csv` (таск 25, ревью таска 24:
/// квант потери ~1000 записей, молчать нельзя); батч очищается в любом
/// случае.
fn flush_symbol_batch(state: &mut SymbolState) -> Result<(), crate::binlog::BinlogError> {
    if state.batch.is_empty() {
        return Ok(());
    }
    let n = state.batch.len() as u64;
    let result = state.writer.write_frame(&state.batch).and_then(|()| {
        state
            .writer
            .flush()
            .map_err(crate::binlog::BinlogError::from)
    });
    state.batch.clear();
    match result {
        Ok(()) => {
            state.records_written += n;
            Ok(())
        }
        Err(e) => {
            state.frames_failed += 1;
            Err(e)
        }
    }
}

/// Гистограмма фиксированной ёмкости для перцентилей задержки (таск 24,
/// критерий «не `Vec` всех замеров»): `Vec<i64>` копил один `i64` на **каждое**
/// сообщение сессии без потолка — `docs/findings/collector-2026-09-12.md`,
/// «Замер до»: рост RSS на пилоте не был плоским, и это одна из накопленных
/// причин (`391 403` кадров × 2 счётчика × 8 байт на пятиминутке, часы на
/// многочасовом пилоте). Здесь — фиксированный массив `TOTAL_BINS` бинов,
/// аллоцированный один раз при создании (`[u64; N]` внутри структуры, не
/// `Vec`) и никогда не растущий: `record`/`percentile` не аллоцируют.
///
/// Бины — логарифмическая шкала по основанию 2 на целых числах (без `f64`
/// и без `log2` на горячем пути): октава — позиция старшего бита значения,
/// внутри октавы `BINS_PER_OCTAVE` = 64 линейных под-бинов по следующим
/// `SUB_BITS` = 6 битам. Бин `(октава, sub)` покрывает
/// `[(64 + sub) · 2^(октава − 6), (65 + sub) · 2^(октава − 6))`, откуда его
/// относительная ширина — `1 / (64 + sub)`, то есть **не хуже 1/64 = 1.5625%
/// от значения** (`RESOLUTION_PCT`, точная верхняя граница, не оценка).
/// `OCTAVES` = 48 переживает удвоения от 1 нс до 2^48 нс (≈ 78 часов) —
/// заведомо больше, чем длина любой сессии, включая шестичасовой пилот;
/// значение вне диапазона насыщает в крайний бин, не паникует. Перцентиль —
/// «ближайший ранг» (`rank = ceil(p/100 · n)`, 1-based), тот же метод, что
/// `bybit::probe::percentile_of_sorted` уже применяет к отсортированному
/// `Vec` RTT — не второй, несовместимый расчёт того же самого; отдаваемое
/// значение — нижняя граница найденного бина, поэтому оно не выше точного
/// перцентиля и не ниже его больше, чем на ширину бина (см. выше).
struct LatencyHistogram {
    bins: [u64; Self::TOTAL_BINS],
    count: u64,
}

impl LatencyHistogram {
    const SUB_BITS: u32 = 6;
    const BINS_PER_OCTAVE: u32 = 1 << Self::SUB_BITS;
    const OCTAVES: u32 = 48;
    const TOTAL_BINS: usize = (Self::BINS_PER_OCTAVE * Self::OCTAVES) as usize;
    /// Разрешение бина в процентах — печатается рядом с перцентилем, чтобы
    /// число в `session.json`/stderr несло свою собственную точность, а не
    /// выглядело точнее, чем оно есть.
    #[allow(clippy::cast_precision_loss)]
    const RESOLUTION_PCT: f64 = 100.0 / Self::BINS_PER_OCTAVE as f64;

    fn new() -> Self {
        Self {
            bins: [0u64; Self::TOTAL_BINS],
            count: 0,
        }
    }

    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    fn bin_of(ns: i64) -> usize {
        if ns <= 0 {
            return 0;
        }
        let v = ns as u64;
        let octave = 63 - v.leading_zeros();
        if octave >= Self::OCTAVES {
            return Self::TOTAL_BINS - 1;
        }
        let mask = u64::from(Self::BINS_PER_OCTAVE - 1);
        let sub = if octave >= Self::SUB_BITS {
            (v >> (octave - Self::SUB_BITS)) & mask
        } else {
            (v << (Self::SUB_BITS - octave)) & mask
        };
        (octave * Self::BINS_PER_OCTAVE) as usize + sub as usize
    }

    #[allow(clippy::cast_possible_truncation, clippy::cast_possible_wrap)]
    fn bin_lower_bound_ns(bin: usize) -> i64 {
        let octave = (bin / Self::BINS_PER_OCTAVE as usize) as u32;
        let sub = (bin % Self::BINS_PER_OCTAVE as usize) as u64;
        let mantissa = u64::from(Self::BINS_PER_OCTAVE) | sub;
        let v = if octave >= Self::SUB_BITS {
            mantissa << (octave - Self::SUB_BITS)
        } else {
            mantissa >> (Self::SUB_BITS - octave)
        };
        v as i64
    }

    fn record(&mut self, ns: i64) {
        self.bins[Self::bin_of(ns)] += 1;
        self.count += 1;
    }

    fn len(&self) -> u64 {
        self.count
    }

    fn percentile(&self, p: u8) -> Option<i64> {
        if self.count == 0 {
            return None;
        }
        let rank = (u64::from(p) * self.count).div_ceil(100).max(1);
        let mut seen: u64 = 0;
        for (bin, &c) in self.bins.iter().enumerate() {
            seen += c;
            if seen >= rank {
                return Some(Self::bin_lower_bound_ns(bin));
            }
        }
        Some(Self::bin_lower_bound_ns(Self::TOTAL_BINS - 1))
    }
}

/// Период замера CPU/RSS в первый час — гейт GC `PLAN.md` 6.1 дословно:
/// «RSS раз в 30 с в течение прогона, плоский». Тот же шаг, которым
/// измерены все прогоны до сих пор (таск 20, пилот, таск 24) — ряды
/// сравнимы между собой.
const RESOURCE_SAMPLE_SECS: u64 = 30;

/// Расписание замеров ряда `samples` по прошедшему времени прогона (таск
/// 25, олвейс-он): первый час — раз в `RESOURCE_SAMPLE_SECS` (гейт 6.1,
/// окно, в котором и живут все 5-минутные замеры), дальше — раз в
/// `HOURLY_REFRESH_SECS`, вместе с переписыванием `session.json`. Без
/// прореживания ряд рос бы без потолка — 2 880 замеров в сутки, ≈ 130 Б
/// каждый в JSON и ≈ 100 Б в памяти: сотни КБ в сутки в файле, который
/// переписывается целиком каждый час, и медленный рост RSS у процесса,
/// чей гейт — «RSS плоский» (тот же довод, которым таск 24 заменил `Vec`
/// задержек гистограммой). Между двумя часовыми записями `session.json`
/// 30-секундный ряд до диска и так не доходил — часовая точка и есть
/// разрешение артефакта после первого часа; потолок — 120 + 24 замера в
/// сутки. Оба периода существующие, новых чисел нет.
fn resource_sample_period(elapsed_s: u64) -> Duration {
    if elapsed_s < HOURLY_REFRESH_SECS {
        Duration::from_secs(RESOURCE_SAMPLE_SECS)
    } else {
        Duration::from_secs(HOURLY_REFRESH_SECS)
    }
}

/// Замер CPU (% одного ядра — критерий GC "< 5% ядра суммарно",
/// `PLAN.md`, раздел GC) и RSS по расписанию `resource_sample_period` — в
/// ряд `session.json.samples` (таск 25: не stderr — «одна строка в час», не
/// строка на замер). `cpu_pct` — среднее между двумя соседними замерами
/// ряда (30 с в первый час, час дальше). Фоновый ОС-поток — приём
/// `bybit::verify_sidecar` ("поток откреплён", `JoinHandle` наружу не идёт:
/// остановка вместе с процессом, а не по сигналу); не в горячем пути, туда
/// попадает только сон и один вызов ОС. Без сторонней зависимости
/// (`sysinfo` не в `Cargo.toml`, добавлять нельзя — `interfaces.md`): то,
/// что уже даёт ОС — `Get-Process` на Windows, `/proc/self/{stat,status}`
/// на Linux.
fn spawn_resource_sampler(out: Arc<Mutex<Vec<ResourceSample>>>) {
    let pid = std::process::id();
    std::thread::spawn(move || {
        let started = std::time::Instant::now();
        let mut prev: Option<(std::time::Instant, f64)> = None;
        loop {
            std::thread::sleep(resource_sample_period(started.elapsed().as_secs()));
            let Some((cpu_seconds, rss_bytes)) = sample_resources(pid) else {
                eprintln!("session: замер CPU/RSS недоступен на этой ОС");
                return;
            };
            let now = std::time::Instant::now();
            let cpu_pct = prev.and_then(|(prev_at, prev_cpu)| {
                let wall_s = now.duration_since(prev_at).as_secs_f64();
                (wall_s > 0.0).then(|| (cpu_seconds - prev_cpu).max(0.0) / wall_s * 100.0)
            });
            prev = Some((now, cpu_seconds));
            if let Ok(mut v) = out.lock() {
                v.push(ResourceSample {
                    ts_utc: ts_utc_of_ns(SystemClock.now_ns()),
                    rss_bytes,
                    cpu_pct,
                });
            }
        }
    });
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
mod tests;
