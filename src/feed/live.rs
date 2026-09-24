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
//! жадно, пока суммарная длина `args` (`ws::pool_args_chars`, **три** топика
//! на инструмент с T45: быстрый стакан `.50`, глубокий `.200` и лента) не
//! упрётся в `MAX_ARGS_CHARS`. Числа «символов на
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
    MAX_CONNECTIONS_PER_5MIN, ORDERBOOK_DEPTH, SUBSCRIBED_DEPTHS,
};

/// Потоки стакана по умолчанию для всех входов, кроме коллектора сессии:
/// один быстрый `.50` (T45). Массив из одного элемента — потому что
/// `spawn_io_thread` берёт срез потоков, а не отдельную глубину.
const FAST_DEPTHS: [u32; 1] = [ORDERBOOK_DEPTH];

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
    /// Источник не меняется на ходу (таск 34, A8.1): реплей читает уже
    /// записанный бинлог — ни добавить в него инструмент, ни снять.
    StaticSource,
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
            Self::StaticSource => {
                f.write_str("источник событий не меняется на ходу (реплей бинлога)")
            }
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
/// соединение» вычисляется из длин имён конкретного пула и набора потоков
/// `depths` (у коллектора сессии их два, у остальных — один), а не
/// задаётся. Группа никогда не пуста: первый инструмент кладётся в неё
/// безусловно (инструмент, чьи топики сами длиннее предела, у Bybit
/// существовать не может — имя символа это единицы символов).
pub(crate) fn plan_connections(
    pool: &[PoolMember],
    depths: &[u32],
) -> Result<Vec<Vec<u16>>, LayoutError> {
    if pool.is_empty() {
        return Err(LayoutError::EmptyPool);
    }
    let mut groups: Vec<Vec<u16>> = Vec::new();
    let mut current: Vec<u16> = Vec::new();
    let mut chars = 0usize;
    for (i, m) in pool.iter().enumerate() {
        let cost = crate::bybit::ws::pool_args_chars(depths, &m.symbol);
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
/// `depths` — тот же набор потоков, по которому считалась раскладка: число
/// топиков на инструмент и длина `args` обязаны быть про то соединение,
/// которое действительно откроется, а не про подписку коллектора.
fn print_topic_budget(pool: &[PoolMember], groups: &[Vec<u16>], depths: &[u32]) {
    let widest = groups
        .iter()
        .map(|g| {
            g.iter()
                .map(|&i| crate::bybit::ws::pool_args_chars(depths, &pool[usize::from(i)].symbol))
                .sum::<usize>()
        })
        .max()
        .unwrap_or(0);
    eprintln!(
        "session: {} инструментов -> {} соединений (по потоку ввода-вывода на каждое), \
{} топика на инструмент; самое полное соединение — {widest} символов args против предела \
{MAX_ARGS_CHARS} (Bybit v5 linear: числа args в запросе нет, только эта длина); бюджет \
создания соединений — {MAX_CONNECTIONS_PER_5MIN} за 5 минут на домен",
        pool.len(),
        groups.len(),
        depths.len() + 1,
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
/// Это и есть сигнал-заменитель для тестов: остановка по Ctrl+C и SIGTERM
/// (`stop_on_signals`) идёт тем же путём, не своим.
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

    /// Ctrl+C и (на Unix) SIGTERM → `stop()` — тем же путём, что файл
    /// `<root>/stop`: сброс писателей и `session.json closed=true`, а не
    /// обрыв на ≤ 10 с и обрезанном кадре. Отдельный ОС-поток со своим
    /// однопоточным рантаймом только ради сигналов (`tokio` уже с
    /// `signal` в `Cargo.toml`, новой зависимости нет): ни рантайм
    /// ввода-вывода, ни поток решений сигнал не слушают.
    ///
    /// **SIGTERM (A8.2, 2026-09-18).** Его шлют `systemctl stop`/`restart`,
    /// перезагрузка и `kill` по умолчанию — до этой правки ловился только
    /// Ctrl+C, и штатным путём запись не закрывалась. На Windows SIGTERM нет
    /// (`cfg(unix)`), там остаётся Ctrl+C.
    ///
    /// Второе нажатие — аварийный выход кодом 130 (128 + SIGINT,
    /// соглашение оболочек, не изобретённое число): после регистрации
    /// обработчика Ctrl+C больше не убивает процесс сам, и если мягкая
    /// остановка застряла, оператору нужен выход, а не зависший терминал.
    pub fn stop_on_signals(self) {
        self.stop_on_signals_ready(None);
    }

    /// Тот же путь с меткой «обработчики зарегистрированы» — шов для теста
    /// SIGTERM: тест посылает сигнал себе **после** метки, иначе сигнал до
    /// регистрации убил бы тестовый процесс. Продовый вход зовёт без метки.
    fn stop_on_signals_ready(self, ready: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>) {
        std::thread::spawn(move || {
            let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            else {
                return;
            };
            let kind = runtime.block_on(wait_for_stop_signal(ready.as_deref()));
            eprintln!("session: {kind} — останавливаюсь (второе нажатие Ctrl+C — аварийный выход)");
            self.stop();
            if runtime.block_on(tokio::signal::ctrl_c()).is_ok() {
                std::process::exit(130);
            }
        });
    }
}

/// Первый из сигналов остановки: Ctrl+C, а на Unix ещё и SIGTERM — строкой,
/// чтобы оператор видел причину. Регистрация обработчика SIGTERM происходит
/// здесь же, до первого `.await` (`signal()` — синхронный вызов): тест ждёт
/// метку готовности ровно по этой точке.
async fn wait_for_stop_signal(ready: Option<&std::sync::atomic::AtomicBool>) -> &'static str {
    use std::sync::atomic::Ordering;
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        match signal(SignalKind::terminate()) {
            Ok(mut term) => {
                if let Some(ready) = ready {
                    ready.store(true, Ordering::SeqCst);
                }
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => "Ctrl+C",
                    _ = term.recv() => "SIGTERM",
                }
            }
            Err(_) => {
                // Обработчик не встал — остаётся Ctrl+C, как было: молча
                // потерять штатную остановку нельзя, поэтому и метка ставится
                // (тест тогда проверит то, что есть, а не повиснет).
                if let Some(ready) = ready {
                    ready.store(true, Ordering::SeqCst);
                }
                let _ = tokio::signal::ctrl_c().await;
                "Ctrl+C"
            }
        }
    }
    #[cfg(not(unix))]
    {
        if let Some(ready) = ready {
            ready.store(true, Ordering::SeqCst);
        }
        let _ = tokio::signal::ctrl_c().await;
        "Ctrl+C"
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
    /// Шарды ввода-вывода: поток и инструменты, которые он обслуживает
    /// (V12, 2026-09-17). Символы нужны, чтобы смерть потока была видна **по
    /// инструментам** — `gaps.csv` знает символ, а не «шард №3»; `handle`
    /// снимается после первого сообщения о смерти, чтобы не повторяться на
    /// каждом тике.
    shards: Vec<IoShard>,
    /// События о смертях шардов, ждущие выдачи (V12): `next_event` отдаёт по
    /// одному, а умерший шард несёт несколько инструментов.
    pending: std::collections::VecDeque<Event>,
    /// Сколько инструментов уже в пуле — следующий добавленный получает
    /// этот индекс (`DynamicPool::add`, таск 34).
    pool_len: usize,
    /// Фабрика соединения, сохранённая со старта (таск 34): те же
    /// коннектор, часы, набор потоков стакана и общий канал, что у стартовых
    /// шардов, — партия, добавленная на ходу, поднимается ровно тем же путём,
    /// что и первая.
    spawn_shard: ShardSpawner,
    /// Потоки стакана этого `Feed` (T45) — тот же набор, что ушёл в
    /// соединения: партия, добавленная на ходу (`DynamicPool::add`), обязана
    /// считаться и подписываться по нему же.
    depths: Vec<u32>,
    /// Таймер потока решений (таск 25) — свой ОС-поток (A8.1), а не рантайм
    /// первого шарда: снятие инструмента останавливает произвольный шард, а
    /// тик — свойство `Feed`, не сокета. Раньше тик жил на первом шарде, и
    /// его смерть уносила с собой сброс кадров, проверку файла пула и
    /// остановку по `stop` — теперь таймер не зависит ни от одного сокета.
    /// Поток завершается сам, когда канал закрылся (ушёл `LiveFeed` и все
    /// шарды); ручка не читается — смерть таймера без тика неотличима от
    /// тишины, а ломаться в нём нечему (свой рантайм, `interval` и отправка
    /// в канал).
    _ticker: Option<std::thread::JoinHandle<()>>,
}

/// Поднимает один шард — ОС-поток с рантаймом и `Connection` — для группы
/// инструментов; коннектор строится по первому инструменту группы (продовый
/// путь его аргумент не читает). Возвращает пару «поток — сигнал остановки»
/// (A8.1): отправитель обязан жить вместе с шардом (`IoShard::stop`), иначе
/// закрытый канал остановил бы поток сразу после старта. Ящик, а не
/// generic-метод: `LiveFeed` не параметризован ни транспортом, ни часами, а
/// добавлять и снимать инструменты на ходу нужно тем же транспортом и теми же
/// часами, что были поданы при старте.
type ShardSpawner =
    Box<dyn FnMut(Vec<SymbolSpec>, &PoolMember) -> (std::thread::JoinHandle<()>, ShardStop) + Send>;

/// Сигнал остановки шарда (A8.1) — отправитель конца `oneshot`.
type ShardStop = tokio::sync::oneshot::Sender<()>;

/// Шард ввода-вывода: ОС-поток и инструменты, которые он обслуживает (V12,
/// 2026-09-17). Без списка инструментов смерть потока осталась бы безымянной:
/// у `Event::Gap` символ — обязательная колонка. Те же `SymbolSpec` нужны
/// снятию с записи (A8.1): шард сокета собирается заново из оставшихся, и
/// **те же** индексы обязаны переехать в новый сокет (индекс события — это
/// позиция в пуле, её смена отправила бы кадр в чужой файл).
struct IoShard {
    handle: Option<std::thread::JoinHandle<()>>,
    specs: Vec<SymbolSpec>,
    /// Остановка шарда (A8.1): без неё снятый инструмент продолжал бы слать
    /// кадры (и дублировал бы данные, если то же имя тут же вернули в пул).
    /// `None` — шард уже остановлен (`remove` или `report_dead_shards`).
    stop: Option<tokio::sync::oneshot::Sender<()>>,
}

/// Группы `SymbolSpec` по раскладке `plan_connections` для партии
/// `members`, индексы которой начинаются с `first_index` (0 на старте,
/// длина пула — при добавлении). Один код и для старта, и для `add`.
/// `depths` — потоки стакана этого `Feed`: по ним считается длина `args`.
fn shard_specs(
    members: &[PoolMember],
    first_index: usize,
    depths: &[u32],
) -> Result<Vec<Vec<SymbolSpec>>, LayoutError> {
    let groups = plan_connections(members, depths)?;
    let total = first_index.saturating_add(members.len());
    if u16::try_from(total.saturating_sub(1)).is_err() {
        return Err(LayoutError::PoolTooLarge { got: total });
    }
    Ok(groups
        .iter()
        .map(|group| {
            group
                .iter()
                .map(|&i| {
                    let m = &members[usize::from(i)];
                    SymbolSpec {
                        symbol: m.symbol.clone(),
                        tick_e9: m.tick_e9,
                        step_e9: m.step_e9,
                        // Индекс продолжает нумерацию пула: раскладка
                        // считала партию с нуля, `Feed` видит её со
                        // сдвигом на всё, что уже подписано.
                        index: u16::try_from(first_index + usize::from(i))
                            .expect("проверено выше: total - 1 влезает в u16"),
                    }
                })
                .collect()
        })
        .collect())
}

/// ОС-поток одного соединения: свой `current_thread`-рантайм, `Connection`
/// на группу инструментов, события — в общий канал через `TaggedSink`.
/// `stop` — сигнал завершения (A8.1): `remove` снимает с записи инструмент
/// шарда, и старый сокет обязан замолчать, а не дожить до конца процесса
/// (иначе события снятого символа шли бы в закрытый файл, а вернувшийся в
/// пул символ получил бы **два** потока одних и тех же данных). Выход из
/// `block_on` роняет рантайм, а вместе с ним — задачу `Connection::run`.
/// `depths` — потоки стакана этого `Feed` (T45): их задаёт вызывающий, потому
/// что книга и приёмники у каждого свои — коллектор сессии ведёт по два потока
/// на инструмент, все остальные (реакция, замер, рекордер) — один.
fn spawn_io_thread<C, K>(
    symbols: Vec<SymbolSpec>,
    connector: C,
    clock: K,
    tx: mpsc::Sender<Item>,
    stop: tokio::sync::oneshot::Receiver<()>,
    depths: &[u32],
) -> std::thread::JoinHandle<()>
where
    C: TransportConnector + 'static,
    K: Clock + Send + 'static,
{
    let backoff = LIVE_BACKOFF;
    let ping = LIVE_PING_INTERVAL;
    let depths = depths.to_vec();
    std::thread::spawn(move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("session: не поднялся однопоточный рантайм ввода-вывода");
        runtime.block_on(async move {
            let cfg = PoolConnConfig {
                symbols,
                // T45: коллектор сессии несёт оба потока стакана по каждому
                // инструменту — быстрый `.50` и глубокий `.200` — одним
                // сокетом. Остальные вызывающие (`lob react`, одно-символьные
                // `lob record`/`pick::measure`) остаются на `.50`: их книга
                // одна и поток не различает.
                depths,
                ping_interval: ping,
                // V5 (2026-09-17): молчание дольше двух пингов — соединение
                // считается мёртвым; иначе полуживой сокет висел бы вечно,
                // без разрыва и без строки `gaps.csv`.
                recv_timeout: ping * 2,
                backoff,
            };
            let sink = TaggedSink { tx: tx.clone() };
            let conn = Connection::new(connector, cfg);
            let run = tokio::spawn(conn.run(clock, sink));
            drop(tx);
            // Смерть `Connection::run` (построение соединения, конец задач) —
            // свой выход; сигнал остановки — свой. Оба конца ведут к концу
            // `block_on`, то есть к закрытию рантайма и потока.
            tokio::select! {
                _ = run => {}
                _ = stop => {}
            }
        });
    })
}

/// Таймер потока решений (таск 25) — **свой** ОС-поток: раз в `period` в
/// общий канал идёт `Event::Tick`, и при полном молчании пула (сеть упала,
/// бэкофф) поток решений просыпается не позже `period`. До A8.1 таймер жил на
/// рантайме **первого** шарда, но снятие инструмента останавливает любой
/// шард, а тик — свойство `Feed`, не сокета: остановка соединения не имеет
/// права уносить сброс кадров, проверку файла пула и остановку по `stop`.
/// Тик по-прежнему один на `Feed` (второй таймер дал бы вдвое больше тиков
/// без нового смысла); поток завершается, когда канал закрылся (все
/// отправители — шарды и сам `LiveFeed` — ушли).
fn spawn_ticker<K>(
    period: Duration,
    clock: K,
    tx: mpsc::Sender<Item>,
) -> std::thread::JoinHandle<()>
where
    K: Clock + Send + 'static,
{
    std::thread::spawn(move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("session: не поднялся однопоточный рантайм таймера");
        runtime.block_on(async move {
            // Таймер рантайма, не «по приходу события»: первый тик
            // `interval` отдаёт сразу — пропускаем его, дальше ровно
            // раз в `period`. Отставший потребитель (полный канал)
            // получает тики реже — `MissedTickBehavior::Delay`, не
            // очередь из тиков.
            let mut ticker = tokio::time::interval(period);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            ticker.tick().await;
            loop {
                ticker.tick().await;
                let ts_ns = clock.now_ns();
                if tx.send(Item::Tick { ts_ns }).await.is_err() {
                    return;
                }
            }
        });
    })
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
    ///
    /// **Единственный вход с двумя потоками стакана** (T45): тик есть только
    /// у коллектора сессии (`commands::lob::session::run_session`), и именно
    /// ему нужны и `.50`, и `.200` (`SUBSCRIBED_DEPTHS`). Остальные входы
    /// (`spawn`, `spawn_with`, `spawn_with_clock`) подписываются на один
    /// быстрый `.50`: их потребители ведут одну книгу и потока не различают.
    pub fn spawn_with_ticks(pool: Vec<PoolMember>, tick: Duration) -> Result<Self, LayoutError> {
        Self::spawn_with_clock_and_connector_and_ticks(
            pool,
            |_member| BybitPublicLinearConnector,
            SystemClock,
            Some(tick),
            SUBSCRIBED_DEPTHS.to_vec(),
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
        F: FnMut(&PoolMember) -> C + Send + 'static,
    {
        Self::spawn_with_clock_and_connector(pool, make_connector, SystemClock)
    }

    /// Общее ядро `spawn`/`spawn_with`/`spawn_with_clock`: и транспорт, и
    /// часы — параметры, ничего больше в теле не меняется. Потоки стакана —
    /// один быстрый `.50` (`FAST_DEPTHS`): двухпотоковый вход один и назван
    /// по имени (`spawn_with_ticks`, T45).
    fn spawn_with_clock_and_connector<C, F, K>(
        pool: Vec<PoolMember>,
        make_connector: F,
        clock: K,
    ) -> Result<Self, LayoutError>
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C + Send + 'static,
        K: Clock + Clone + Send + 'static,
    {
        Self::spawn_with_clock_and_connector_and_ticks(
            pool,
            make_connector,
            clock,
            None,
            FAST_DEPTHS.to_vec(),
        )
    }

    /// Шов теста для тика и остановки (таск 25): фейковый транспорт, свои
    /// часы, свой период тика и свой набор потоков стакана (T45).
    pub(crate) fn spawn_with_clock_and_connector_and_ticks<C, F, K>(
        pool: Vec<PoolMember>,
        mut make_connector: F,
        clock: K,
        tick: Option<Duration>,
        depths: Vec<u32>,
    ) -> Result<Self, LayoutError>
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C + Send + 'static,
        K: Clock + Clone + Send + 'static,
    {
        let groups = plan_connections(&pool, &depths)?;
        print_topic_budget(&pool, &groups, &depths);
        let capacity = CHANNEL_CAPACITY_PER_SYMBOL.saturating_mul(pool.len().max(1));
        let (tx, rx) = mpsc::channel::<Item>(capacity);
        let stop_tx = tx.clone();
        let gap_clock = clock.clone();

        // Раскладка посчитана до того, как что-либо открыто: группы —
        // глобальные индексы пула, порядок пула не меняется.
        let shards = shard_specs(&pool, 0, &depths)?;

        // Таймер потока решений — своим ОС-потоком (A8.1): тик общий для
        // всего `Feed` (окно потери кадра), а снятие инструмента
        // останавливает произвольный шард — тик обязан это пережить.
        let ticker = tick.map(|period| spawn_ticker(period, clock.clone(), tx.clone()));
        let mut shards_io = Vec::with_capacity(shards.len());
        for symbols in shards {
            let first = &pool[usize::from(symbols[0].index)];
            // Коннектор создаётся по первому инструменту группы — как и
            // раньше, один на соединение (продовый путь его аргумент не
            // читает вовсе).
            let connector = make_connector(first);
            let (shard_stop, stop_rx) = tokio::sync::oneshot::channel();
            let specs = symbols.clone();
            let handle = spawn_io_thread(
                symbols,
                connector,
                clock.clone(),
                tx.clone(),
                stop_rx,
                &depths,
            );
            shards_io.push(IoShard {
                handle: Some(handle),
                specs,
                stop: Some(shard_stop),
            });
        }

        // Фабрика на будущее (таск 34): партия, добавленная на ходу, идёт
        // через тот же коннектор, те же часы, тот же набор потоков и тот же
        // канал. Тик у неё свой не заводится — таймер один на `Feed`.
        let shard_tx = tx.clone();
        let shard_clock = clock;
        let shard_depths = depths.clone();
        let spawn_shard: ShardSpawner = Box::new(move |symbols, first| {
            let (shard_stop, stop_rx) = tokio::sync::oneshot::channel();
            let handle = spawn_io_thread(
                symbols,
                make_connector(first),
                shard_clock.clone(),
                shard_tx.clone(),
                stop_rx,
                &shard_depths,
            );
            (handle, shard_stop)
        });
        drop(tx);

        Ok(Self {
            rx,
            tx: stop_tx,
            now_ns: Box::new(move || gap_clock.now_ns()),
            stopped: false,
            shards: shards_io,
            pending: std::collections::VecDeque::new(),
            pool_len: pool.len(),
            spawn_shard,
            depths,
            _ticker: ticker,
        })
    }
}

impl super::DynamicPool for LiveFeed {
    /// Новая партия — новые соединения (таск 34): раскладка той же
    /// `plan_connections` с тем же пределом `MAX_ARGS_CHARS` и тем же
    /// набором потоков стакана (`self.depths`, T45 — иначе длина `args` и
    /// подписка новой партии разошлись бы со стартовыми), индексы —
    /// продолжение пула (`pool_len..`), живые сокеты не трогаются.
    /// Аллокации здесь — раз на добавление, не на событие рынка: после
    /// возврата новый шард шлёт в тот же канал тем же `TaggedSink`.
    /// `StopHandle` гасит и его: `Stop` идёт по общему каналу, а ОС-поток
    /// нового соединения умирает вместе с процессом, как и стартовые.
    fn add(&mut self, members: Vec<PoolMember>) -> Result<Vec<u16>, LayoutError> {
        let shards = shard_specs(&members, self.pool_len, &self.depths)?;
        let mut indices = Vec::with_capacity(members.len());
        for symbols in shards {
            let first = &members[usize::from(symbols[0].index) - self.pool_len];
            indices.extend(symbols.iter().map(|s| s.index));
            let specs = symbols.clone();
            let (handle, stop) = (self.spawn_shard)(symbols, first);
            self.shards.push(IoShard {
                handle: Some(handle),
                specs,
                stop: Some(stop),
            });
        }
        self.pool_len += members.len();
        Ok(indices)
    }

    /// Снятие с записи (A8.1): инструменты уходят из пула — их соединение
    /// замолкает. Инструменты одного сокета разделяют подписку, поэтому
    /// «убрать один» технически означает «пересоздать шард без него»:
    /// оставшиеся переезжают в новый сокет **с теми же индексами** (индекс —
    /// позиция в пуле, её смена отправила бы кадр в чужой файл), а прежний
    /// поток получает сигнал остановки и завершается вместе со своим
    /// рантаймом. Цена разделяемого соединения — короткий разрыв у
    /// оставшихся: он **виден** (A8.1b) — возвращаемые индексы соседей
    /// вызывающий пишет строками `gaps.csv`. `pool_len` не уменьшается:
    /// индексы не переиспользуются, и вернувшийся в пул символ получает новый
    /// (`add`), а не чужой старый.
    fn remove(&mut self, symbols: &[u16]) -> Result<Vec<u16>, LayoutError> {
        // Сначала разбираем шарды (нужен `&mut self` на пересоздание —
        // поэтому сбор остатков отдельным шагом, без заимствования).
        let mut kept_shards: Vec<IoShard> = Vec::with_capacity(self.shards.len());
        let mut respawn: Vec<(usize, Vec<SymbolSpec>)> = Vec::new();
        // Соседи, чей сокет пересобран: их шов покрытия — наружу.
        let mut seams: Vec<u16> = Vec::new();
        for shard in self.shards.drain(..) {
            let IoShard {
                handle,
                specs,
                stop,
            } = shard;
            if !specs.iter().any(|s| symbols.contains(&s.index)) {
                kept_shards.push(IoShard {
                    handle,
                    specs,
                    stop,
                });
                continue;
            }
            // Снятый символ (или весь шард) — сигнал остановки: старый
            // поток не должен дожить до конца процесса, иначе вернувшийся
            // в пул символ получил бы два потока одних и тех же данных.
            if let Some(stop) = stop {
                let _ = stop.send(());
            }
            // Барьер `stop` → `spawn` (A8.1b, ревью 18.09): новый сокет на те
            // же индексы поднимается только после фактического выхода старого
            // потока — иначе оба коротко живы на одних топиках и шлют дубли
            // дельт, а книга на них уходит в ресинк. `join` здесь висеть не
            // может: поток ждёт `select!` над `Connection::run` и приёмником
            // сигнала, сигнал отправлен выше, блокирующих задач в рантайме
            // нет — выход из `block_on` роняет рантайм вместе с потоком.
            if let Some(handle) = handle {
                let _ = handle.join();
            }
            let kept: Vec<SymbolSpec> = specs
                .into_iter()
                .filter(|s| !symbols.contains(&s.index))
                .collect();
            if kept.is_empty() {
                continue;
            }
            // Соседи переезжают в новый сокет — у них шов покрытия: индексы
            // наружу, вызывающий пишет по строке `gaps.csv` на каждый.
            seams.extend(kept.iter().map(|s| s.index));
            respawn.push((kept_shards.len(), kept.clone()));
            kept_shards.push(IoShard {
                handle: None,
                specs: kept,
                stop: None,
            });
        }
        for (slot, specs) in respawn {
            let first = PoolMember {
                symbol: specs[0].symbol.clone(),
                tick_e9: specs[0].tick_e9,
                step_e9: specs[0].step_e9,
            };
            let (handle, stop) = (self.spawn_shard)(specs, &first);
            kept_shards[slot].handle = Some(handle);
            kept_shards[slot].stop = Some(stop);
        }
        self.shards = kept_shards;
        Ok(seams)
    }
}

impl LiveFeed {
    /// V12 (2026-09-17): смерть ОС-потока шарда раньше не была видна никому —
    /// инструменты просто замолкали, и заметить это можно было только по
    /// переставшему расти графику. Проверка дешёвая (`is_finished`, без
    /// ожидания) и стоит **на тике**, не на событии рынка: своего события у
    /// умершего потока уже не будет. Сообщается один раз на шард — строкой
    /// `gaps.csv` на каждый его инструмент, как у разрыва сокета (первый
    /// инструмент — `Disconnected`, остальные — `DisconnectedSameSocket`),
    /// поэтому `reconnects` растёт на один за шард, а не на инструмент.
    fn report_dead_shards(&mut self, ts_ns: i64) {
        for shard in &mut self.shards {
            let dead = shard
                .handle
                .as_ref()
                .is_some_and(std::thread::JoinHandle::is_finished);
            if !dead {
                continue;
            }
            shard.handle = None;
            shard.stop = None;
            for (i, spec) in shard.specs.iter().enumerate() {
                self.pending.push_back(Event::Gap {
                    symbol: spec.index,
                    local_ts_ns: ts_ns,
                    kind: if i == 0 {
                        GapKind::Disconnected
                    } else {
                        GapKind::DisconnectedSameSocket
                    },
                    depth: None,
                    silence_ns: None,
                    first_of_episode: false,
                    detail: "ОС-поток шарда ввода-вывода завершился — его инструменты \
                             больше не получают данных"
                        .to_string(),
                });
            }
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
        // Смерти шардов, замеченные прошлым тиком, отдаются по одной: у
        // `next_event` одно событие на вызов (V12).
        if let Some(ev) = self.pending.pop_front() {
            return Some(ev);
        }
        let (idx, conn_event) = match self.rx.blocking_recv()? {
            Item::Conn(idx, ev) => (idx, ev),
            Item::Tick { ts_ns } => {
                self.report_dead_shards(ts_ns);
                return Some(Event::Tick { local_ts_ns: ts_ns });
            }
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
                // Неразобранный кадр — ничей: из него нельзя прочитать ни
                // символа, ни потока.
                depth: None,
                silence_ns: None,
                first_of_episode: false,
                detail: format!("кадр не разобрался: {err:?}"),
            },
            ConnEvent::SequenceGap {
                depth,
                expected,
                got,
            } => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: GapKind::SequenceGap,
                depth: Some(depth),
                silence_ns: None,
                first_of_episode: false,
                detail: format!(
                    "разрыв u потока .{depth}: ждали {expected}, пришло {got} — ресинк снапшотом"
                ),
            },
            ConnEvent::BookInvariantViolated { depth, err } => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: GapKind::BookInvariant,
                depth: Some(depth),
                silence_ns: None,
                first_of_episode: false,
                detail: format!("книга потока .{depth} нарушена: {err:?} — ресинк снапшотом"),
            },
            ConnEvent::Disconnected { first_of_socket } => Event::Gap {
                symbol: idx,
                local_ts_ns: (self.now_ns)(),
                kind: if first_of_socket {
                    GapKind::Disconnected
                } else {
                    GapKind::DisconnectedSameSocket
                },
                // Разрыв сокета роняет **оба** потока этого инструмента
                // сразу: у него нет одной глубины.
                depth: None,
                silence_ns: None,
                first_of_episode: false,
                detail: "транспорт переподключился — шов покрытия".to_string(),
            },
            ConnEvent::ConnectFailed {
                local_ts_ns,
                attempt,
                http_status,
                err,
            } => {
                Event::Gap {
                    symbol: idx,
                    local_ts_ns,
                    kind: GapKind::ConnectFailed,
                    // Сокет не открылся — потока нет ни у одного из них.
                    depth: None,
                    silence_ns: None,
                    first_of_episode: false,
                    detail: match http_status {
                        Some(status) => {
                            format!("connect() отклонён биржей: HTTP {status} — {err} (попытка {attempt})")
                        }
                        None => format!("connect() не удался: {err} (попытка {attempt})"),
                    },
                }
            }
            ConnEvent::Unrouted { local_ts_ns } => Event::Gap {
                symbol: idx,
                local_ts_ns,
                kind: GapKind::Unrouted,
                depth: None,
                silence_ns: None,
                first_of_episode: false,
                detail: "топик кадра не сопоставлен ни одному инструменту сокета".to_string(),
            },
            ConnEvent::SubscribeFailed {
                local_ts_ns,
                topic,
                ret_msg,
            } => Event::Gap {
                symbol: idx,
                local_ts_ns,
                kind: GapKind::SubscribeFailed,
                // Отказ подписки — про инструмент целиком: биржа не
                // согласовала топик, данных не будет ни у одного потока.
                // Какой именно топик отказан — в детали.
                depth: None,
                silence_ns: None,
                first_of_episode: false,
                detail: match topic {
                    Some(topic) => format!("подписка не состоялась: {topic} — {ret_msg}"),
                    None => format!("подписка не состоялась: {ret_msg}"),
                },
            },
            // V5, доработка 2026-09-24: тишина рынка — не потеря кадра и не
            // разрыв (решение владельца: соединение по ней не рвётся), но
            // должна быть видна — счётчик эпизодов и максимум длительности
            // (`session.json`), тем же приёмом, что `Unrouted`.
            ConnEvent::MarketSilence {
                local_ts_ns,
                silence_ns,
                first_of_episode,
            } => Event::Gap {
                symbol: idx,
                local_ts_ns,
                kind: GapKind::MarketSilence,
                depth: None,
                silence_ns: Some(silence_ns),
                first_of_episode,
                detail: format!(
                    "рыночных событий (Book/Trade) нет {silence_ns} нс — соединение живо, не рвётся"
                ),
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
