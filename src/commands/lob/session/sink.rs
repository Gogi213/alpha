//! Путь события на диск: приёмник файла части (`FrameSink`, один `write_all`
//! на кадр), состояние инструмента (`SymbolState` — по одному `StreamState` на
//! поток глубины, T45), запись книжного события и сделки в накопитель кадра,
//! сброс батча и гистограмма задержек фиксированной ёмкости. Горячий путь
//! сессии — семь запретов `interfaces.md` действуют здесь целиком.
//!
//! **Два потока — два независимых состояния записи (T45).** Быстрый
//! (`orderbook.50`) пишет `<root>/<SYMBOL>-<день>.binlog` ровно как до T45,
//! глубокий (`orderbook.200`) — `<root>/deep/<SYMBOL>-<день>.binlog`; у каждого
//! своя книга, свой `u`-контроль (в `bybit::conn`), свой первый кадр-снапшот и
//! свой счётчик разрывов. Поток события выбирается глубиной из топика
//! (`book::Update::depth`), а не конфигурацией: `event_stream` — единственное
//! место этого решения.

use std::fs::File;
use std::io::{Seek, SeekFrom};

use crate::binlog::{Record, Writer};
use crate::book::{Book, Side};
use crate::bybit::conn::{FAST_STREAM, ORDERBOOK_DEEP_DEPTH, SUBSCRIBED_DEPTHS};
use crate::commands::record::FRAME_TARGET_RECORDS;
use crate::feed::live::PoolMember;

/// Сколько потоков глубины ведёт сессия — длина `SUBSCRIBED_DEPTHS`
/// (`bybit::conn`): быстрый `.50` и глубокий `.200`.
pub(super) const STREAM_COUNT: usize = SUBSCRIBED_DEPTHS.len();

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

/// Самое крупное сообщение этой сессии в записях: снапшот глубокого потока —
/// `orderbook.200` отдаёт не более двухсот уровней на сторону (документация
/// Bybit v5, `bybit::conn::ORDERBOOK_DEEP_DEPTH`), то есть до `2 × 200`
/// записей одним событием. Быстрый поток (`2 × 50`) в это число влезает.
pub(super) const SESSION_MAX_MESSAGE_RECORDS: usize = 2 * ORDERBOOK_DEEP_DEPTH as usize;

/// Самый крупный кадр этой сессии: порог батча плюс самое крупное
/// сообщение (снапшот глубокого потока, см. выше) — то же слагаемое, что у
/// ёмкости `batch`. Кадр синтетического снапшота ротации
/// (`push_book_snapshot` — по записи на уровень обеих сторон своей книги)
/// закрыт этим же потолком с запасом, и не потому, что он «мельче
/// сообщения», а потому, что его длина ограничена ёмкостью книги: `book::
/// CAPACITY` уровней на сторону, то есть не больше `2 × CAPACITY` записей
/// (512 при `CAPACITY = 256`) и никогда больше `SESSION_MAX_FRAME_RECORDS`.
/// Ограничение задаёт именно потолок кадра, а не размер снапшота.
pub(super) const SESSION_MAX_FRAME_RECORDS: usize =
    FRAME_TARGET_RECORDS + SESSION_MAX_MESSAGE_RECORDS;

/// Ёмкость `scratch` потока глубины: снапшот обеих сторон этого потока —
/// `2 × depth` записей. Это верхняя граница сообщения **потока** по
/// построению топика (`.50` — не глубже пятидесяти уровней на сторону,
/// `.200` — не глубже двухсот); дельта, назвавшая больше уровней, чем
/// снапшот, лишь вырастит `Vec` один раз, а не потеряется и не исказится.
pub(super) fn stream_scratch_capacity(depth: u32) -> usize {
    2 * depth as usize
}

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

/// Состояние одного потока глубины одного инструмента: своя книга, свой файл
/// части, свой контроль достоверности и свои счётчики (T45). Ровно то, чем был
/// `SymbolState` до T45; разделение по потокам — потому что разрыв `u` в
/// глубоком потоке не имеет права ни считаться разрывом быстрого, ни гасить
/// его `synced`, ни трогать его файл.
pub(super) struct StreamState {
    /// Глубина потока из `SUBSCRIBED_DEPTHS` (50 — быстрый, 200 — глубокий):
    /// имя топика `orderbook.<depth>.<symbol>`, которым этот поток обновляют.
    pub(super) depth: u32,
    pub(super) writer: Writer<FrameSink>,
    /// Номер части и индекс суток текущего файла (`ts / NS_PER_DAY`, как
    /// `record::Recorder::day_index` — целочисленное деление на событие,
    /// строка даты только на ротации).
    pub(super) part: u32,
    pub(super) day_index: i64,
    /// Книга потока — ради синтетического снапшота первым кадром новых суток
    /// (таск 25, как `record::Recorder::ensure_day` + `on_snapshot`): без него
    /// файл суток начинался бы с дельт, и `verify`/`levels` отбросили бы сутки
    /// целиком. Таск 24 снял вторую книгу как лишнюю проверку — здесь она не
    /// проверка, а источник первого кадра; `apply` на дельту — ноль аллокаций
    /// (гейт таска 24).
    pub(super) book: Book,
    /// Книга потока доверена с последнего снапшота биржи (сброс на любом `Gap`
    /// ресинка/переподключения **этого** потока).
    pub(super) synced: bool,
    /// В текущем файле уже лежит первый кадр-снапшот; до него дельты и
    /// сделки в файл не идут (`record::RecordError::NoSnapshot`, тот же
    /// контракт) — иначе сутки нечитаемы.
    pub(super) has_snapshot: bool,
    pub(super) records_written: u64,
    /// Ротация суток не удалась (`claim_part_with`): следующая попытка не
    /// раньше этой метки — окно `FRAME_LOSS_WINDOW_SECS`, чтобы отказ диска
    /// не давал строку `gaps.csv` на каждое событие; до неё события новых
    /// суток идут в текущую часть.
    pub(super) rotate_retry_after_ns: i64,
    /// Кадров этого потока, которые не записались (таск 25): каждый — строка
    /// `gaps.csv` `write_failed`, квант потери — до `FRAME_TARGET_RECORDS`
    /// записей.
    pub(super) frames_failed: u64,
    /// Скретч-буфер `write_market_event` — переиспользуется на каждое
    /// событие вместо `Vec::new()`, иначе горячий путь аллоцирует ровно там,
    /// где гейт GC требует ноль (`interfaces.md`, запрет 1; было ТУПИКОМ 1
    /// таска 04). Ёмкость — снапшот обеих сторон своего потока
    /// (`stream_scratch_capacity`); `.clear()` в начале `write_market_event`
    /// не освобождает ёмкость, только длину.
    pub(super) scratch: Vec<Record>,
    /// Накопитель кадра (таск 24, критерий «батчинг как в `lob record`»):
    /// `write_market_event` переносит `scratch` сюда через `Vec::append`
    /// (перемещение элементов, не копия — `scratch` пустеет, ёмкость цела) и
    /// пишет кадр `binlog::Writer`, только когда здесь накопилось
    /// `FRAME_TARGET_RECORDS` записей или пришёл тик (`Event::Tick`, окно
    /// `FRAME_LOSS_WINDOW_SECS`). Раньше здесь писался кадр на **каждое**
    /// сообщение — `docs/findings/collector-2026-09-12.md`, «Замер до»:
    /// кадр из 1–5 записей почти не сжимается. Ёмкость с запасом на самое
    /// крупное сообщение сверх порога (`SESSION_MAX_FRAME_RECORDS`), чтобы
    /// приход этого сообщения ровно на границе порога не вызвал
    /// перевыделение до `flush_symbol_batch`.
    pub(super) batch: Vec<Record>,
}

impl StreamState {
    /// Новый поток с уже открытой частью. `depth` — из `SUBSCRIBED_DEPTHS`,
    /// `writer`/`part` — из `claim_part_with` по каталогу **своего** потока.
    pub(super) fn new(
        depth: u32,
        member: &PoolMember,
        writer: Writer<FrameSink>,
        part: u32,
        day_index: i64,
    ) -> Self {
        Self {
            depth,
            writer,
            part,
            day_index,
            book: Book::new(member.tick_e9, member.step_e9),
            synced: false,
            has_snapshot: false,
            records_written: 0,
            rotate_retry_after_ns: i64::MIN,
            frames_failed: 0,
            scratch: Vec::with_capacity(stream_scratch_capacity(depth)),
            batch: Vec::with_capacity(SESSION_MAX_FRAME_RECORDS),
        }
    }
}

/// Состояние одного инструмента пула: своя книга и свой файл **на каждый
/// поток** глубины (`streams`, порядок — `SUBSCRIBED_DEPTHS`). Индекс в
/// `SessionCtx.states` — тот же `symbol: u16`, которым `Feed` метит каждое
/// событие (`interfaces.md`: тег события — не строка, лукап по строке на
/// каждое событие был бы `HashMap` на пути события, запрет 7); слот потока —
/// `depth` события.
pub(super) struct SymbolState {
    pub(super) member: PoolMember,
    pub(super) streams: [StreamState; STREAM_COUNT],
}

/// Слот потока по глубине из топика (`book::Update::depth`). Линейный поиск
/// по двум элементам — ни `HashMap`, ни аллокаций на событие (запрет 7).
/// `None` — поток не нашего набора (`bybit::conn::SUBSCRIBED_DEPTHS`).
pub(super) fn stream_of_depth(depth: u32) -> Option<usize> {
    SUBSCRIBED_DEPTHS.iter().position(|&d| d == depth)
}

/// Поток, в который идёт событие: книга — по глубине из топика, сделка —
/// всегда быстрый (лента `publicTrade` не делится по глубине стакана), а
/// служебное сообщение не идёт никуда. Единственное место этого решения —
/// и ротация суток, и запись спрашивают его, а не каждая свой.
pub(super) fn event_stream(payload: &crate::bybit::ws::Event) -> Option<usize> {
    match payload {
        crate::bybit::ws::Event::Book(update) => stream_of_depth(update.depth),
        crate::bybit::ws::Event::Trade(_) => Some(FAST_STREAM),
        crate::bybit::ws::Event::Other => None,
    }
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

/// Синтетический полный снапшот из книги потока первым кадром новых
/// суток (таск 25, Decision 7 / `record::Recorder::on_snapshot`): по
/// записи на уровень обеих сторон, флаги снапшота. Книга — своя у каждого
/// потока, поэтому и снапшот у каждого свой: `.50` пишет пятьдесят уровней,
/// `.200` — двести.
pub(super) fn push_book_snapshot(stream: &mut StreamState, exch_ts_ns: i64, local_ts_ns: i64) {
    stream.batch.clear();
    for side in [Side::Bid, Side::Ask] {
        for (tick, lots) in stream.book.levels(side) {
            stream.batch.push(Record {
                ev: hftbacktest_flags(side, true),
                exch_ts_ns,
                local_ts_ns,
                price_ticks: tick,
                qty_lots: lots,
                block: false,
                rpi: false,
            });
        }
    }
    stream.has_snapshot = true;
}

/// Одна книжная запись/сделка → в накопитель кадра **потока**
/// (`StreamState::batch`); кадр на диск — `flush_symbol_batch` по порогу
/// `FRAME_TARGET_RECORDS` или сразу на снапшоте (как `Recorder::
/// on_snapshot`: первый кадр файла не ждёт тика); третий повод — тик
/// (`SessionCtx::on_tick`). Раньше эта функция сама писала `binlog::
/// Writer::write_frame` на **каждое** сообщение — `docs/findings/
/// docs/findings/collector-2026-09-12.md`, «Замер до». `Err` — кадр не записался
/// (счётчик `frames_failed` уже увеличен, батч очищен).
///
/// Поток события выбирает сам (`event_stream`): книга — по глубине из
/// топика, сделка — быстрый поток. Служебное (`Other`) и глубина не нашего
/// набора не пишутся никуда.
///
/// Книга потока (`stream.book`) ведётся ради синтетического снапшота
/// на ротации суток (таск 25), не как вторая проверка: `bybit::conn::
/// handle_raw` уже применил то же обновление к своей книге и не пересылает
/// ничего, что не прошло `apply`, поэтому `apply` здесь на той же
/// последовательности не может дать другого исхода (таск 24) — а если даёт
/// (`Err`), это дефект, и книга помечается недоверенной, не молчит.
/// До первого снапшота в файле дельты и сделки не пишутся (как `record::
/// RecordError::NoSnapshot`): файл, начатый с дельт, нечитаем целиком.
/// Ни одно поле `Record` не читает состояние книги: цена/размер строятся
/// из самого `update`/`trade`.
pub(super) fn write_market_event(
    state: &mut SymbolState,
    local_ts_ns: i64,
    payload: crate::bybit::ws::Event,
) -> Result<(), crate::binlog::BinlogError> {
    use hftbacktest::types::{LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT};

    let Some(slot) = event_stream(&payload) else {
        return Ok(());
    };
    // Шаги читаются у инструмента (они общие для обоих потоков — один
    // `instruments.csv` на символ), а состояние ниже — уже поток: у потока
    // нет своего `member`, и адресоваться к нему через `StreamState` значило
    // бы дублировать шаги в каждом потоке.
    let (tick_e9, step_e9) = (state.member.tick_e9, state.member.step_e9);
    let state = &mut state.streams[slot];
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
                        price_ticks: price_e9 / tick_e9,
                        qty_lots: qty_e9 / step_e9,
                        block: false,
                        rpi: false,
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
                price_ticks: trade.price_e9 / tick_e9,
                qty_lots: trade.qty_e9 / step_e9,
                block: trade.block,
                rpi: trade.rpi,
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

/// Пишет накопленный `batch` потока одним кадром `binlog::Writer` и сразу
/// сбрасывает приёмник (`FrameSink::flush` — один `write_all` на кадр:
/// файл на диске кончается на границе кадра), опустошает батч (`.clear()`
/// — длина в ноль, ёмкость цела). Пустой батч — no-op, тот же контракт, что
/// `binlog::Writer::write_frame`. Ошибка — наружу: вызывающий считает
/// `frames_failed` и пишет строку `gaps.csv` (таск 25, ревью таска 24:
/// квант потери ~1000 записей, молчать нельзя); батч очищается в любом
/// случае.
pub(super) fn flush_symbol_batch(
    state: &mut StreamState,
) -> Result<(), crate::binlog::BinlogError> {
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
pub(super) struct LatencyHistogram {
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
    pub(super) const RESOLUTION_PCT: f64 = 100.0 / Self::BINS_PER_OCTAVE as f64;

    pub(super) fn new() -> Self {
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

    pub(super) fn record(&mut self, ns: i64) {
        self.bins[Self::bin_of(ns)] += 1;
        self.count += 1;
    }

    pub(super) fn len(&self) -> u64 {
        self.count
    }

    pub(super) fn percentile(&self, p: u8) -> Option<i64> {
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

#[cfg(test)]
mod hot_path_guard {
    /// C3 аудита 2026-09-17: у `sink.rs` греп-теста не было. Банится ровно то,
    /// что запрещено на пути события: `HashMap`/`BTreeMap` — лукап по символу
    /// идёт по `u16` (запрет 7). `f64` здесь **разрешён** и не банится: он
    /// живёт в `RESOLUTION_PCT` — константе печати перцентиля гистограммы, а не
    /// в цене или размере; строки собраны из частей, иначе литерал триггерил бы
    /// проверку сам на себе.
    #[test]
    fn event_path_uses_no_maps() {
        const SRC: &str = include_str!("sink.rs");
        let banned = [concat!("Hash", "Map"), concat!("BTree", "Map")];
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
