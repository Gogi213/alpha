//! `Feed` с сокета (A5). Один и тот же интерфейс, что и `replay::ReplayFeed`
//! — только источник теперь пул WS-подключений Bybit, по одному на
//! инструмент. `bybit::conn::Connection` не мой файл (задача 04 не трогает
//! `bybit/conn.rs`) и уже даёт ровно то, что нужно: сокет за трейтом
//! `Transport`, ресинк, `local_ts` до разбора (H12) — и он однозначно
//! однопимвольный (`ConnConfig.symbol: String`), поэтому пул из N
//! инструментов — это N подключений, не одно с N топиками.
//!
//! **Лимит топиков (критерий приёмки таска 04).** У Bybit v5 нет общего
//! числового потолка на количество топиков одного публичного подключения:
//! документация (`https://bybit-exchange.github.io/docs/v5/ws/connect`,
//! проверено 2026-09-11) называет предел только для Spot (10 args на запрос
//! подписки) и Options (2000 args), а для Futures/Spread прямо пишет «no
//! args limit for now»; единственная граница, общая для всех категорий —
//! 21000 символов суммарной длины `args` **одного** подключения. Наш поток
//! — `linear` (Futures), и каждое подключение этого модуля несёт всего два
//! топика (`orderbook.50.<symbol>`, `publicTrade.<symbol>`), то есть предел
//! не то что не достигается — он не имеет значения при N=10. `print_topic_budget`
//! печатает это число перед стартом, а не молчит про него: критерий требует
//! «лимит топиков проверяется», не «пул всегда влезает в одно соединение».
//! Если бы пул вырос настолько, что имело смысл слить несколько инструментов
//! в одно подключение (сейчас — нет: `Connection` этого не умеет, и это не
//! моя правка), эта функция — то место, где предел проверяется перед тем,
//! как решать, объединять топики или нет.
//!
//! **Один поток ввода-вывода на N подписок.** Рантайм токио — один,
//! `current_thread`, поднят на своём собственном потоке ОС; подключений
//! внутри может быть несколько, но каждое из них — задача этого же
//! однопоточного рантайма, не отдельный поток. Один общий канал уносит
//! события всех подключений в поток решения. `crossbeam` не в `Cargo.toml`
//! (проверено: `Cargo.toml` не содержит `crossbeam`) — `bybit::conn.rs` уже
//! отступил от `ARCHITECTURE.md` по этой же причине и использует
//! `tokio::sync::mpsc` с `blocking_recv()` на стороне потребителя; здесь то
//! же самое решение, а не второе. Недостающая зависимость — `BLOCKED`, не
//! установка (`interfaces.md`).

use std::time::Duration;

use tokio::sync::mpsc;

use crate::bybit::conn::{
    BackoffConfig, BybitPublicLinearConnector, Clock, ConnConfig, ConnEvent, ConnSink, Connection,
    SystemClock, TransportConnector,
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

/// Верхняя граница `args` одного публичного подключения Bybit — факт
/// протокола (см. doc модуля), не измеренное и не назначенное здесь число.
const BYBIT_ARGS_CHAR_LIMIT: usize = 21_000;

fn combined_args_len(pool: &[PoolMember]) -> usize {
    pool.iter()
        .map(|m| {
            format!("orderbook.50.{}", m.symbol).len() + format!("publicTrade.{}", m.symbol).len()
        })
        .sum()
}

/// Печатает бюджет топиков пула на stderr — критерий приёмки «лимит
/// топиков проверяется; при упоре — несколько соединений, печатается».
pub fn print_topic_budget(pool: &[PoolMember]) {
    let combined = combined_args_len(pool);
    eprintln!(
        "session: топики — {} подключений по 2 топика (orderbook.50 + publicTrade); \
         объединённые в одно подключение потребовали бы {combined} символов args против \
         предела {BYBIT_ARGS_CHAR_LIMIT} (Bybit v5 linear: числового предела по топикам \
         сейчас нет, только этот)",
        pool.len(),
    );
    if combined > BYBIT_ARGS_CHAR_LIMIT {
        eprintln!(
            "session: пул не влез бы в одно подключение — используется несколько \
             (уже используется: одно подключение на инструмент)"
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
    idx: u8,
    tx: mpsc::Sender<Item>,
}

impl ConnSink for TaggedSink {
    fn send_event(&self, ev: ConnEvent) -> impl std::future::Future<Output = ()> + Send {
        let idx = self.idx;
        let tx = self.tx.clone();
        async move {
            let _ = tx.send(Item::Conn(idx, ev)).await;
        }
    }
}

/// Элемент общего канала (таск 25): событие соединения, тик таймера
/// рантайма ввода-вывода или остановка. Все три идут одной очередью FIFO —
/// `Stop` доходит до потока решений **после** всего, что было прислано
/// раньше него, поэтому остановка ничего не теряет из уже принятого.
enum Item {
    Conn(u8, ConnEvent),
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

/// Живой `Feed`: одно подключение на инструмент пула, один общий канал в
/// поток решения. `next_event` блокирует поток решения на канале
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
    _io_thread: std::thread::JoinHandle<()>,
}

impl LiveFeed {
    /// Продовый вход: одно подключение на инструмент, реальный сокет,
    /// системные часы (`SystemClock`) — прежнее поведение, потребитель не
    /// собирает свои метки этапов из отдельного `Clock` (`lob session`).
    pub fn spawn(pool: Vec<PoolMember>) -> Self {
        Self::spawn_with_clock(pool, SystemClock)
    }

    /// Продовый вход с тиком таймера (таск 25): раз в `tick` рантайм
    /// ввода-вывода кладёт в общий канал `Event::Tick` — и при полном
    /// молчании пула (сеть упала, бэкофф) поток решений просыпается не
    /// позже `tick`. Период задаёт вызывающий: это его окно потери
    /// (`commands::record::FRAME_LOSS_WINDOW_SECS`), не свойство сокета.
    pub fn spawn_with_ticks(pool: Vec<PoolMember>, tick: Duration) -> Self {
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
    pub fn spawn_with_clock<K>(pool: Vec<PoolMember>, clock: K) -> Self
    where
        K: Clock + Clone + Send + 'static,
    {
        Self::spawn_with_clock_and_connector(pool, |_member| BybitPublicLinearConnector, clock)
    }

    /// Обобщённый вход (шов теста 1, `interfaces.md`: `Transport` —
    /// фейковый сокет). `make_connector` вызывается один раз на инструмент,
    /// на старте — как и продовый путь, который каждому `Connection` даёт
    /// свой `BybitPublicLinearConnector`. Часы — `SystemClock`, как и прежде;
    /// `spawn_with_clock_and_connector` — тот же приём с явным `Clock`.
    pub fn spawn_with<C, F>(pool: Vec<PoolMember>, make_connector: F) -> Self
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
    ) -> Self
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
    ) -> Self
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C,
        K: Clock + Clone + Send + 'static,
    {
        print_topic_budget(&pool);
        let capacity = CHANNEL_CAPACITY_PER_SYMBOL.saturating_mul(pool.len().max(1));
        let (tx, rx) = mpsc::channel::<Item>(capacity);
        let stop_tx = tx.clone();
        let gap_clock = clock.clone();
        let tick_clock = clock.clone();
        let tick_tx = tx.clone();
        let connectors: Vec<(u8, PoolMember, C)> = pool
            .into_iter()
            .enumerate()
            .map(|(i, m)| {
                let connector = make_connector(&m);
                (i as u8, m, connector)
            })
            .collect();

        let io_thread = std::thread::spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("session: не поднялся однопоточный рантайм ввода-вывода");
            runtime.block_on(async move {
                let mut tasks = Vec::new();
                for (idx, member, connector) in connectors {
                    let cfg = ConnConfig {
                        symbol: member.symbol,
                        tick_e9: member.tick_e9,
                        step_e9: member.step_e9,
                        ping_interval: LIVE_PING_INTERVAL,
                        backoff: LIVE_BACKOFF,
                    };
                    let sink = TaggedSink {
                        idx,
                        tx: tx.clone(),
                    };
                    let conn = Connection::new(connector, cfg);
                    tasks.push(tokio::spawn(conn.run(clock.clone(), sink)));
                }
                if let Some(period) = tick {
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
        });

        Self {
            rx,
            tx: stop_tx,
            now_ns: Box::new(move || gap_clock.now_ns()),
            stopped: false,
            _io_thread: io_thread,
        }
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
            ConnEvent::Disconnected => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: GapKind::Disconnected,
                detail: "транспорт переподключился — шов покрытия".to_string(),
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
mod tests {
    use super::*;
    use crate::bybit::conn::{Frame, Transport, TransportError};
    use std::collections::VecDeque;
    use std::future::Future;
    use std::sync::{Arc, Mutex};

    /// Тот же приём, что и в тестах `bybit::conn`: очередь фреймов на
    /// приём. Своя копия, не импорт — исходная `ScriptedTransport` приватна
    /// тестовому модулю того файла (шов 1 `interfaces.md`: «`Transport` —
    /// фейковый сокет», не обязательно один и тот же экземпляр на весь
    /// репозиторий).
    struct ScriptedTransport {
        inbox: Arc<Mutex<VecDeque<Result<Frame, TransportError>>>>,
    }

    impl Transport for ScriptedTransport {
        async fn send_text(&mut self, _msg: String) -> Result<(), TransportError> {
            Ok(())
        }

        fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
            let next = self.inbox.lock().unwrap().pop_front();
            async move {
                match next {
                    Some(item) => item,
                    None => std::future::pending().await,
                }
            }
        }
    }

    struct OneShotConnector {
        inbox: Arc<Mutex<VecDeque<Result<Frame, TransportError>>>>,
    }

    impl TransportConnector for OneShotConnector {
        type Transport = ScriptedTransport;
        fn connect(
            &mut self,
        ) -> impl Future<Output = Result<ScriptedTransport, TransportError>> + Send {
            let inbox = self.inbox.clone();
            async move { Ok(ScriptedTransport { inbox }) }
        }
    }

    /// Критерий приёмки таска 04: «оборванная сессия теряет кадр, не
    /// сессию». Первый фрейм не разбирается вовсе (не JSON) — это должно
    /// дать `Event::Gap`, а не оборвать поток; второй фрейм — валидный
    /// снапшот книги — должен дойти следующим событием как ни в чём не
    /// бывало на том же `Feed`.
    #[test]
    fn broken_frame_is_a_gap_not_a_dead_session() {
        let snapshot = r#"{"topic":"orderbook.50.BTCUSDT","type":"snapshot","ts":1,"data":{"s":"BTCUSDT","b":[["100.0","1.0"]],"a":[],"u":1,"seq":1}}"#;
        let inbox = Arc::new(Mutex::new(VecDeque::from(vec![
            Ok(Frame::Text("не json вовсе".to_string())),
            Ok(Frame::Text(snapshot.to_string())),
        ])));
        let pool = vec![PoolMember {
            symbol: "BTCUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }];
        let mut feed = LiveFeed::spawn_with(pool, |_m| OneShotConnector {
            inbox: inbox.clone(),
        });

        let first = feed.next_event().expect("битый кадр обязан дойти как Gap");
        assert!(
            matches!(first, Event::Gap { symbol: 0, .. }),
            "первый фрейм не json — обязан прийти как Gap, а не оборвать поток: {first:?}"
        );

        let second = feed
            .next_event()
            .expect("сессия обязана продолжиться следующим событием");
        assert!(
            matches!(second, Event::Market { symbol: 0, .. }),
            "второй, валидный кадр обязан дойти как Market после потерянного первого: {second:?}"
        );
    }

    /// Часы, поставленные в `Clock`, а не вычислены заново. Счётчик
    /// возвращает малые, заведомо не похожие на `SystemTime` значения
    /// (реальная эпоха — порядка 10^18 нс в 2026 году); если `local_ts_ns`
    /// пришёл маленьким, `Connection::run` использовал именно этот `Clock`,
    /// а не жёстко зашитый `SystemClock` — ровно то, что чинит домен часов
    /// таска 15 (`interfaces.md`, «Из таска 15»): `lob react` обязан подать
    /// свой `MonotonicClock` сюда же, что и ставит стадии книги/триггера/
    /// send, иначе `local_ts_ns`/`parsed_ts_ns` остаются в чужом домене.
    #[derive(Clone)]
    struct FakeSeqClock(Arc<std::sync::atomic::AtomicI64>);

    impl crate::bybit::conn::Clock for FakeSeqClock {
        fn now_ns(&self) -> i64 {
            self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
        }
    }

    #[test]
    fn spawn_with_clock_feeds_the_injected_clock_into_the_connection() {
        let snapshot = r#"{"topic":"orderbook.50.BTCUSDT","type":"snapshot","ts":1,"data":{"s":"BTCUSDT","b":[["100.0","1.0"]],"a":[],"u":1,"seq":1}}"#;
        let inbox = Arc::new(Mutex::new(VecDeque::from(vec![Ok(Frame::Text(
            snapshot.to_string(),
        ))])));
        let pool = vec![PoolMember {
            symbol: "BTCUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }];
        let clock = FakeSeqClock(Arc::new(std::sync::atomic::AtomicI64::new(0)));
        let mut feed = LiveFeed::spawn_with_clock_and_connector(
            pool,
            |_m| OneShotConnector {
                inbox: inbox.clone(),
            },
            clock,
        );

        let ev = feed.next_event().expect("снапшот обязан дойти");
        match ev {
            Event::Market { local_ts_ns, .. } => assert!(
                local_ts_ns < 1_000_000,
                "метка обязана прийти из инжектированных часов ({local_ts_ns}), \
                 не из SystemClock (тот дал бы ~10^18)"
            ),
            other => panic!("ждали Market, получили {other:?}"),
        }
    }

    /// Таск 25: при полном молчании транспорта (`pending` навсегда) поток
    /// решений всё равно просыпается тиком таймера рантайма не позже
    /// периода, а `StopHandle::stop()` (сигнал-заменитель Ctrl+C) заканчивает
    /// поток `None` — и окончательно: следующий вызов тоже `None`.
    #[test]
    fn silent_transport_still_ticks_and_stop_ends_the_feed_for_good() {
        let inbox = Arc::new(Mutex::new(VecDeque::new()));
        let pool = vec![PoolMember {
            symbol: "BTCUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }];
        let clock = FakeSeqClock(Arc::new(std::sync::atomic::AtomicI64::new(0)));
        let mut feed = LiveFeed::spawn_with_clock_and_connector_and_ticks(
            pool,
            |_m| OneShotConnector {
                inbox: inbox.clone(),
            },
            clock,
            Some(Duration::from_millis(20)),
        );
        // Транспорт молчит вечно (`pending`): если тик не по таймеру, этот
        // вызов не вернётся вовсе — зависший тест и есть красный.
        let first = feed
            .next_event()
            .expect("тик обязан прийти без единого кадра");
        assert!(
            matches!(first, Event::Tick { .. }),
            "молчащий транспорт даёт тик, не что-то другое: {first:?}"
        );

        feed.stop_handle().stop();
        // До `Stop` в очереди могут стоять тики — вычерпываем их, `None`
        // обязан наступить, а не зависнуть.
        let mut seen_none = false;
        for _ in 0..1000 {
            if feed.next_event().is_none() {
                seen_none = true;
                break;
            }
        }
        assert!(seen_none, "после stop() поток обязан закончиться None");
        assert!(feed.next_event().is_none(), "остановка окончательна");
    }
}
