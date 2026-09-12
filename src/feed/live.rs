//! `Feed` с сокета (A5). Один и тот же интерфейс, что и `replay::ReplayFeed`
//! — только источник теперь пул WS-подключений Bybit. `bybit::conn::
//! Connection` даёт сокет за трейтом `Transport`, ресинк и `local_ts` до
//! разбора (H12) и с таска 28 несёт **много** инструментов на одно
//! подключение (`PoolConnConfig`), а не один: сколько именно — считает
//! `plan_connections` ниже.
//!
//! **Раскладка подписок по соединениям (таск 28).** Соединение больше не
//! равно инструменту: `bybit::conn::Connection` несёт список инструментов
//! (`PoolConnConfig`), маршрут события — символ из его топика. Причина —
//! два предела Bybit v5 (`https://bybit-exchange.github.io/docs/v5/ws/
//! connect`, проверено 2026-09-12), и они тянут в разные стороны:
//!
//! - «Do not build over 500 connections in 5 minutes. This is counted per
//!   WebSocket domain» (`conn::MAX_CONNECTIONS_PER_5MIN`) — при одном
//!   сокете на инструмент пул в 500 инструментов **сам старт** съедает весь
//!   пятиминутный бюджет домена, и ни один реконнект в него уже не влезает;
//! - «For one public connection, you cannot have length of "args" array
//!   over 21,000 characters» (`conn::MAX_ARGS_CHARS`) — сколько подписок
//!   влезает в один сокет;
//! - «No args limit for Futures and Spread for now» — числа `args` в одном
//!   запросе подписки для linear нет, поэтому весь набор сокета уходит
//!   одним сообщением (`ws::sub_pool`).
//!
//! Отсюда раскладка `plan_connections`: инструменты набиваются в сокет
//! жадно, пока суммарная длина `args` (`ws::pool_args_chars`, оба топика
//! на инструмент) не упрётся в `MAX_ARGS_CHARS`. Числа «символов на
//! соединение» в коде нет — оно вычисляется из имён пула и предела
//! площадки: замер 2026-09-12 — восемь инструментов дали одно соединение
//! (326 символов `args`), 761 (весь linear USDT-перпетуальный список) — два
//! (самое полное 20 982 символа). Спека таска 04 («по соединению на
//! инструмент») отменена этим замером —
//! `docs/findings/collector-2026-09-12.md`, раздел «500».
//!
//! **Потоки ввода-вывода.** Один ОС-поток на соединение, в каждом свой
//! `current_thread`-рантайм: сокет, который ушёл в бэкофф или разбирает
//! шторм снапшотов после ресинка, не задерживает `recv` соседнего сокета.
//! Число потоков — то же, что число соединений, то есть тоже вычислено, а
//! не назначено. `ARCHITECTURE.md` A3 не нарушен: поток решений
//! по-прежнему **один** и получает всё одним каналом `tokio::sync::mpsc`
//! (D04, `crossbeam` не в `Cargo.toml`) — `mpsc` многопроизводительный,
//! шардов на приёмной стороне нет и блокировок между ними тоже.

use std::time::Duration;

use tokio::sync::mpsc;

use crate::bybit::conn::{
    BackoffConfig, BybitPublicLinearConnector, Clock, ConnEvent, ConnSink, Connection,
    PoolConnConfig, SymbolSpec, SystemClock, TransportConnector, MAX_ARGS_CHARS,
    MAX_CONNECTIONS_PER_5MIN, ORDERBOOK_DEPTH,
};

use super::{Event, Feed, GapKind};

/// Один инструмент пула для живого потока: символ и шаги его цены/размера
/// из `instruments.csv` (сам пул задача 04 не выбирает — берёт готовым от
/// `lob pick`, `interfaces.md`).
#[derive(Debug, Clone)]
pub struct PoolMember {
    pub symbol: String,
    pub tick_e9: i64,
    pub step_e9: i64,
}

/// Bybit требует периодический пинг — факт протокола (см. `bybit::conn`),
/// не число плана; 20 с — то же значение, что уже стоит в
/// `commands::record` (там не `pub`, поэтому повторено, а не переиспользовано).
const LIVE_PING_INTERVAL: Duration = Duration::from_secs(20);

/// Бэкофф переподключения — эксплуатационное решение вызывающего кода
/// (`bybit::conn::BackoffConfig`, doc на месте объявления), не измеренное
/// число: `commands::record` держит своё, это — своё для сессии по пулу.
const LIVE_BACKOFF: BackoffConfig = BackoffConfig {
    initial: Duration::from_millis(500),
    max: Duration::from_secs(30),
    multiplier: 2,
};

/// Ёмкость канала одного подключения перед объединением в общий канал —
/// тот же порядок, что несёт один сокет в `commands::record`
/// (`CHANNEL_CAPACITY`, там не `pub`).
const CHANNEL_CAPACITY_PER_SYMBOL: usize = 4_096;

/// Почему раскладка может не состояться. Оба случая — дефект вызывающего
/// (пустой или невозможный пул), а не рынка: молча вернуть ноль соединений
/// значило бы поднять `Feed`, который никогда ничего не отдаст, и повесить
/// поток решений на `blocking_recv` навсегда.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LayoutError {
    /// Пул пуст — подписываться не на что.
    EmptyPool,
    /// Инструментов больше, чем помещается в индекс события
    /// (`feed::Event::*::symbol` — `u16`). Схлопывать лишние в `u16::MAX`
    /// нельзя: два инструмента получили бы один индекс и писали бы в один
    /// файл.
    PoolTooLarge { got: usize },
}

impl std::fmt::Display for LayoutError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyPool => {
                f.write_str("раскладка соединений: пул пуст — подписываться не на что")
            }
            Self::PoolTooLarge { got } => write!(
                f,
                "раскладка соединений: {got} инструментов — больше {}, а индекс события u16",
                usize::from(u16::MAX) + 1
            ),
        }
    }
}

impl std::error::Error for LayoutError {}

/// Раскладка пула по соединениям: жадная набивка сокета инструментами,
/// пока суммарная длина `args` не упрётся в `conn::MAX_ARGS_CHARS`.
/// Возвращает группы **глобальных индексов пула** — порядок пула
/// сохраняется, инструмент i остаётся индексом i для `Feed`.
///
/// Единственное число в этой функции — предел площадки; «символов на
/// соединение» вычисляется из длин имён конкретного пула, а не задаётся.
/// Группа никогда не пуста: первый инструмент кладётся в неё безусловно
/// (инструмент, чьи топики сами длиннее предела, у Bybit существовать не
/// может — имя символа это единицы символов).
pub(crate) fn plan_connections(pool: &[PoolMember]) -> Result<Vec<Vec<u16>>, LayoutError> {
    if pool.is_empty() {
        return Err(LayoutError::EmptyPool);
    }
    let mut groups: Vec<Vec<u16>> = Vec::new();
    let mut current: Vec<u16> = Vec::new();
    let mut chars = 0usize;
    for (i, m) in pool.iter().enumerate() {
        let cost = crate::bybit::ws::pool_args_chars(ORDERBOOK_DEPTH, &m.symbol);
        if !current.is_empty() && chars + cost > MAX_ARGS_CHARS {
            groups.push(std::mem::take(&mut current));
            chars = 0;
        }
        chars += cost;
        let idx = u16::try_from(i).map_err(|_| LayoutError::PoolTooLarge { got: pool.len() })?;
        current.push(idx);
    }
    if !current.is_empty() {
        groups.push(current);
    }
    Ok(groups)
}

/// Печатает раскладку на stderr — критерий приёмки «раскладка подписок по
/// соединениям по замеру и ограничениям Bybit, лимит процитирован».
fn print_topic_budget(pool: &[PoolMember], groups: &[Vec<u16>]) {
    let widest = groups
        .iter()
        .map(|g| {
            g.iter()
                .map(|&i| {
                    crate::bybit::ws::pool_args_chars(ORDERBOOK_DEPTH, &pool[usize::from(i)].symbol)
                })
                .sum::<usize>()
        })
        .max()
        .unwrap_or(0);
    eprintln!(
        "session: {} инструментов -> {} соединений (по потоку ввода-вывода на каждое), \
2 топика на инструмент; самое полное соединение — {widest} символов args против предела \
{MAX_ARGS_CHARS} (Bybit v5 linear: числа args в запросе нет, только эта длина); бюджет \
создания соединений — {MAX_CONNECTIONS_PER_5MIN} за 5 минут на домен",
        pool.len(),
        groups.len(),
    );
    if groups.len() > MAX_CONNECTIONS_PER_5MIN {
        eprintln!(
            "session: раскладка требует {} соединений — больше бюджета \
{MAX_CONNECTIONS_PER_5MIN} за 5 минут; переподключения биржа отклонит",
            groups.len()
        );
    }
}

/// `ConnSink` (`bybit::conn`), который приклеивает индекс инструмента и
/// шлёт прямо в общий канал `LiveFeed` — таск 20: раньше между `Connection::
/// run` и этим общим каналом стояли ещё один канал на соединение и отдельная
/// задача-пересыльщик (`while let Some(ev) = conn_rx.recv().await { out.send
/// ((idx, ev)).await }`), единственная работа которой была тегирование;
/// на восьми соединениях это восемь лишних задач планировщика и двойной
/// `mpsc`-переход на каждое сообщение одного ОС-потока. Замер (`lob
/// session`, `queue_p99_ns`, живая сессия `data/recording-eco/
/// 20260911T182544Z-before`) поймал именно этот виток дороже самого разбора
/// JSON: `918.6` мкс p99 против `686.0` мкс p99 разбора. `send_event`
/// клонирует `Sender` (атомарный инкремент счётчика `Arc`, не аллокация
/// кучи для данных) вместо второго `mpsc::send`/`recv` — дешевле и на один
/// меньше `.await`-точку на событие.
struct TaggedSink {
    tx: mpsc::Sender<Item>,
}

impl ConnSink for TaggedSink {
    fn send_event(
        &self,
        symbol_idx: u16,
        ev: ConnEvent,
    ) -> impl std::future::Future<Output = ()> + Send {
        let tx = self.tx.clone();
        async move {
            let _ = tx.send(Item::Conn(symbol_idx, ev)).await;
        }
    }
}

/// Элемент общего канала (таск 25): событие соединения, тик таймера
/// рантайма ввода-вывода или остановка. Все три идут одной очередью FIFO —
/// `Stop` доходит до потока решений **после** всего, что было прислано
/// раньше него, поэтому остановка ничего не теряет из уже принятого.
enum Item {
    Conn(u16, ConnEvent),
    Tick { ts_ns: i64 },
    Stop,
}

/// Ручка остановки живого `Feed` (таск 25): `stop()` кладёт `Item::Stop`
/// в общий канал, после чего `next_event` вернёт `None` — ту же
/// «сессия закончилась», которую вызывающий и так обязан обрабатывать.
/// Это и есть сигнал-заменитель для тестов: остановка по Ctrl+C
/// (`stop_on_ctrl_c`) идёт тем же путём, не своим.
#[derive(Clone)]
pub struct StopHandle {
    tx: mpsc::Sender<Item>,
}

impl StopHandle {
    /// Блокирующая отправка: ждёт места в очереди, а не роняет остановку
    /// на полном канале (`try_send` на 32 768 накопленных событиях
    /// потерял бы её молча). Зовётся не из async-контекста.
    pub fn stop(&self) {
        let _ = self.tx.blocking_send(Item::Stop);
    }

    /// Ctrl+C → `stop()`. Отдельный ОС-поток со своим однопоточным
    /// рантаймом только ради `tokio::signal::ctrl_c` (`tokio` уже с
    /// `signal` в `Cargo.toml`, новой зависимости нет): ни рантайм
    /// ввода-вывода, ни поток решений сигнал не слушают. Второе нажатие —
    /// аварийный выход кодом 130 (128 + SIGINT, соглашение оболочек, не
    /// изобретённое число): после регистрации обработчика Ctrl+C больше не
    /// убивает процесс сам, и если мягкая остановка застряла, оператору
    /// нужен выход, а не зависший терминал.
    pub fn stop_on_ctrl_c(self) {
        std::thread::spawn(move || {
            let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            else {
                return;
            };
            if runtime.block_on(tokio::signal::ctrl_c()).is_err() {
                return;
            }
            eprintln!("session: Ctrl+C — останавливаюсь (второе нажатие — аварийный выход)");
            self.stop();
            if runtime.block_on(tokio::signal::ctrl_c()).is_ok() {
                std::process::exit(130);
            }
        });
    }
}

/// Живой `Feed`: пул разложен по соединениям (`plan_connections`), один
/// общий канал в поток решения. `next_event` блокирует поток решения на канале
/// (`blocking_recv`) — ровно то разделение, которое `ARCHITECTURE.md` A3
/// называет «один поток решений, ввод-вывод отдельно».
pub struct LiveFeed {
    rx: mpsc::Receiver<Item>,
    /// Клон отправителя для `stop_handle` — сам канал закрывается только
    /// вместе с `LiveFeed`, отправители соединений живут на потоке
    /// ввода-вывода.
    tx: mpsc::Sender<Item>,
    /// Те же часы, что метят `local_ts_ns` в соединениях: метка `Gap` без
    /// собственного времени (`Disconnected`, разрыв `u`) ставится здесь,
    /// в том же домене, а не нулём.
    now_ns: Box<dyn Fn() -> i64 + Send>,
    stopped: bool,
    _io_threads: Vec<std::thread::JoinHandle<()>>,
}

impl LiveFeed {
    /// Продовый вход: раскладка `plan_connections`, реальный сокет,
    /// системные часы (`SystemClock`) — прежнее поведение, потребитель не
    /// собирает свои метки этапов из отдельного `Clock` (`lob session`).
    pub fn spawn(pool: Vec<PoolMember>) -> Result<Self, LayoutError> {
        Self::spawn_with_clock(pool, SystemClock)
    }

    /// Продовый вход с тиком таймера (таск 25): раз в `tick` рантайм
    /// ввода-вывода кладёт в общий канал `Event::Tick` — и при полном
    /// молчании пула (сеть упала, бэкофф) поток решений просыпается не
    /// позже `tick`. Период задаёт вызывающий: это его окно потери
    /// (`commands::record::FRAME_LOSS_WINDOW_SECS`), не свойство сокета.
    pub fn spawn_with_ticks(pool: Vec<PoolMember>, tick: Duration) -> Result<Self, LayoutError> {
        Self::spawn_with_clock_and_connector_and_ticks(
            pool,
            |_member| BybitPublicLinearConnector,
            SystemClock,
            Some(tick),
        )
    }

    /// Ручка остановки — см. `StopHandle`.
    pub fn stop_handle(&self) -> StopHandle {
        StopHandle {
            tx: self.tx.clone(),
        }
    }

    /// Продовый вход с явно поданным `Clock` (ремонт таска 15, `interfaces.md`
    /// «Из таска 15»: домен часов). Живой `lob react` ставит свои метки
    /// книги/триггера/send через `bybit::clock::MonotonicClock` — если
    /// `local_ts_ns`/`parsed_ts_ns` этого `Feed` при этом идут через
    /// `SystemClock`, разность стадий смешивает два разных источника
    /// времени и становится бессмысленной (на Windows `SystemTime` вдобавок
    /// не гарантированно монотонны). Подавая сюда тот же экземпляр
    /// `MonotonicClock`, что и стадии `react`, все пять меток одного пути
    /// оказываются в одном домене.
    pub fn spawn_with_clock<K>(pool: Vec<PoolMember>, clock: K) -> Result<Self, LayoutError>
    where
        K: Clock + Clone + Send + 'static,
    {
        Self::spawn_with_clock_and_connector(pool, |_member| BybitPublicLinearConnector, clock)
    }

    /// Обобщённый вход (шов теста 1, `interfaces.md`: `Transport` —
    /// фейковый сокет). `make_connector` вызывается один раз на
    /// **соединение** (не на инструмент), на старте — как и продовый путь,
    /// который каждому `Connection` даёт свой `BybitPublicLinearConnector`. Часы — `SystemClock`, как и прежде;
    /// `spawn_with_clock_and_connector` — тот же приём с явным `Clock`.
    pub fn spawn_with<C, F>(pool: Vec<PoolMember>, make_connector: F) -> Result<Self, LayoutError>
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C,
    {
        Self::spawn_with_clock_and_connector(pool, make_connector, SystemClock)
    }

    /// Общее ядро `spawn`/`spawn_with`/`spawn_with_clock`: и транспорт, и
    /// часы — параметры, ничего больше в теле не меняется.
    fn spawn_with_clock_and_connector<C, F, K>(
        pool: Vec<PoolMember>,
        make_connector: F,
        clock: K,
    ) -> Result<Self, LayoutError>
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C,
        K: Clock + Clone + Send + 'static,
    {
        Self::spawn_with_clock_and_connector_and_ticks(pool, make_connector, clock, None)
    }

    /// Шов теста для тика и остановки (таск 25): фейковый транспорт, свои
    /// часы, свой период тика.
    pub(crate) fn spawn_with_clock_and_connector_and_ticks<C, F, K>(
        pool: Vec<PoolMember>,
        mut make_connector: F,
        clock: K,
        tick: Option<Duration>,
    ) -> Result<Self, LayoutError>
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C,
        K: Clock + Clone + Send + 'static,
    {
        let groups = plan_connections(&pool)?;
        print_topic_budget(&pool, &groups);
        let capacity = CHANNEL_CAPACITY_PER_SYMBOL.saturating_mul(pool.len().max(1));
        let (tx, rx) = mpsc::channel::<Item>(capacity);
        let stop_tx = tx.clone();
        let gap_clock = clock.clone();
        let tick_clock = clock.clone();
        let tick_tx = tx.clone();

        // Раскладка посчитана до того, как что-либо открыто: группы —
        // глобальные индексы пула, порядок пула не меняется.
        let mut shards: Vec<(Vec<SymbolSpec>, C)> = Vec::with_capacity(groups.len());
        for group in &groups {
            let symbols: Vec<SymbolSpec> = group
                .iter()
                .map(|&i| {
                    let m = &pool[usize::from(i)];
                    SymbolSpec {
                        symbol: m.symbol.clone(),
                        tick_e9: m.tick_e9,
                        step_e9: m.step_e9,
                        index: i,
                    }
                })
                .collect();
            // Коннектор создаётся по первому инструменту группы — как и
            // раньше, один на соединение (продовый путь его аргумент не
            // читает вовсе).
            let connector = make_connector(&pool[usize::from(group[0])]);
            shards.push((symbols, connector));
        }

        // Тик кладётся на рантайм **первого** шарда: он общий для всего
        // потока решений (окно потери кадра), а не свойство сокета — второй
        // экземпляр таймера дал бы вдвое больше тиков без нового смысла.
        let mut tick_for_shard = tick;
        let mut io_threads = Vec::with_capacity(shards.len());
        for (symbols, connector) in shards {
            let tx = tx.clone();
            let clock = clock.clone();
            let backoff = LIVE_BACKOFF;
            let ping = LIVE_PING_INTERVAL;
            let shard_tick = tick_for_shard.take();
            let tick_tx = tick_tx.clone();
            let tick_clock = tick_clock.clone();
            io_threads.push(std::thread::spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("session: не поднялся однопоточный рантайм ввода-вывода");
                runtime.block_on(async move {
                    let cfg = PoolConnConfig {
                        symbols,
                        ping_interval: ping,
                        backoff,
                    };
                    let sink = TaggedSink { tx: tx.clone() };
                    let conn = Connection::new(connector, cfg);
                    let mut tasks = vec![tokio::spawn(conn.run(clock, sink))];
                    if let Some(period) = shard_tick {
                        // Таймер рантайма, не «по приходу события»: первый тик
                        // `interval` отдаёт сразу — пропускаем его, дальше ровно
                        // раз в `period`. Отставший потребитель (полный канал)
                        // получает тики реже — `MissedTickBehavior::Delay`, не
                        // очередь из тиков.
                        tasks.push(tokio::spawn(async move {
                            let mut ticker = tokio::time::interval(period);
                            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
                            ticker.tick().await;
                            loop {
                                ticker.tick().await;
                                let ts_ns = tick_clock.now_ns();
                                if tick_tx.send(Item::Tick { ts_ns }).await.is_err() {
                                    return;
                                }
                            }
                        }));
                    }
                    drop(tx);
                    for t in tasks {
                        let _ = t.await;
                    }
                });
            }));
        }
        drop(tx);
        drop(tick_tx);

        Ok(Self {
            rx,
            tx: stop_tx,
            now_ns: Box::new(move || gap_clock.now_ns()),
            stopped: false,
            _io_threads: io_threads,
        })
    }
}

impl Feed for LiveFeed {
    fn next_event(&mut self) -> Option<Event> {
        // `Connection::run` не возвращается сам по себе (переподключается
        // вечно) — единственный способ дойти до `None` здесь: канал
        // закрылся, то есть отправители не пережили процесс (тесты
        // используют это, обрывая задачи вместо мягкой остановки, как и
        // `commands::record` не даёт `Connection::run` останавливаться
        // иначе, кроме как через `conn_task.abort()` снаружи).
        if self.stopped {
            return None;
        }
        let (idx, conn_event) = match self.rx.blocking_recv()? {
            Item::Conn(idx, ev) => (idx, ev),
            Item::Tick { ts_ns } => return Some(Event::Tick { local_ts_ns: ts_ns }),
            Item::Stop => {
                // Остановка окончательна: соединения продолжают слать в
                // канал, но после `Stop` поток закрыт для вызывающего.
                self.stopped = true;
                return None;
            }
        };
        Some(match conn_event {
            ConnEvent::Message {
                local_ts_ns,
                parsed_ts_ns,
                event,
            } => Event::Market {
                symbol: idx,
                local_ts_ns,
                parse_latency_ns: Some(parsed_ts_ns.saturating_sub(local_ts_ns)),
                payload: event,
            },
            ConnEvent::ParseFailed { local_ts_ns, err } => Event::Gap {
                symbol: idx,
                local_ts_ns,
                kind: GapKind::ParseFailed,
                detail: format!("кадр не разобрался: {err:?}"),
            },
            ConnEvent::SequenceGap { expected, got } => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: GapKind::SequenceGap,
                detail: format!("разрыв u: ждали {expected}, пришло {got} — ресинк снапшотом"),
            },
            ConnEvent::BookInvariantViolated { err } => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: GapKind::BookInvariant,
                detail: format!("книга нарушена: {err:?} — ресинк снапшотом"),
            },
            ConnEvent::Disconnected { first_of_socket } => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: if first_of_socket {
                    GapKind::Disconnected
                } else {
                    GapKind::DisconnectedSameSocket
                },
                detail: "транспорт переподключился — шов покрытия".to_string(),
            },
            ConnEvent::Unrouted { local_ts_ns } => Event::Gap {
                symbol: idx,
                local_ts_ns,
                kind: GapKind::Unrouted,
                detail: "топик кадра не сопоставлен ни одному инструменту сокета".to_string(),
            },
        })
    }
}

// -----------------------------------------------------------------------
// Грепом по образцу `lob/levels.rs::module_stays_detached_from_transport_
// clocks_and_approx_numbers`: горячий путь не вправе звать часы напрямую,
// представлять цену или размер числом с плавающей запятой, ни держать
// книгу в хеш-отображении (таск 17, долг таска 15 — «Из ремонта таска 15»,
// греп-тест раньше был только у `strategy.rs`/`react.rs`).
// -----------------------------------------------------------------------

#[cfg(test)]
mod hot_path_guard {
    /// `std::time::Duration` (реконнект-бэкофф на ОС-потоке ввода-вывода,
    /// запрет 3 — не решающий поток) — не запрещён, поэтому банится только
    /// прямой вызов часов, а не сам модуль `std::time`. Строки собраны из
    /// частей — иначе литерал триггерил бы эту же проверку сам на себя.
    #[test]
    fn module_never_calls_the_wall_clock_or_uses_float_prices_or_hashmaps() {
        const SRC: &str = include_str!("live.rs");
        let banned = [
            concat!("Inst", "ant::now"),
            concat!("System", "Time::now"),
            concat!("f", "64"),
            concat!("Hash", "Map"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}

#[cfg(test)]
mod tests;
