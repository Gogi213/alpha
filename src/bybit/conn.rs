//! Живое подключение: подписки, ресинк, local_ts на возврате из recv.
//!
//! `ws.rs` — только разбор, без сокета и без времени (его же собственный
//! заголовок это и говорит). Этот файл — обратное: сокет, часы, переподключение
//! и решение о ресинке. Разделение ровно по этой границе даёт то, что требует
//! задача: сокет спрятан за трейтом `Transport`, и вся логика реконнекта и
//! ресинка гоняется в тестах на сценарном транспорте, без единого обращения
//! к сети — а разбор байт в структуры уже проверен отдельно, в `ws.rs`.
//!
//! **`local_ts` ставится сразу по возврату из `recv`, до разбора** (`H12`,
//! `ASSUMPTION H12`) — метка живёт в этом файле и нигде больше, ровно потому
//! что только здесь есть точка «сразу после `recv`».
//!
//! Книга (`crate::book::Book`) здесь тоже применяется — не для стратегии (та
//! строит свою из событий, которые уходят наружу через канал), а чтобы решить,
//! когда слать `orderbook.50` заново: только у сокета есть право написать в
//! него, поэтому решение "разрыв → ресинк" обязано жить рядом с сокетом, а не
//! на другом конце канала, откуда до сокета не дотянуться.
//!
//! **Отступление от `ARCHITECTURE.md`.** Черновик архитектуры называет канал
//! до потока решений «crossbeam»; в `Cargo.toml` крейта `crossbeam-channel` нет
//! и добавлять его не в этом файле (это чужой файл). Используется
//! `tokio::sync::mpsc`: у него `Receiver::blocking_recv()` устроен буквально
//! для моста «асинхронный производитель — синхронный потребитель», то есть для
//! ровно того же разделения, которое описывает A3 (тут — `tokio`, там — один
//! поток без блокировок). Смысл решения не меняется, меняется имя канала.

use crate::book::{ApplyError, Book};
use crate::bybit::ws::{self, Event};
use std::future::Future;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;
use tokio::time::MissedTickBehavior;

/// Глубина стакана из шага 0.1 плана: буквально `orderbook.50.<symbol>`. Не
/// параметр — это не измеренное число и не порог, а часть протокола, которую
/// задаёт сам план, дословно.
const ORDERBOOK_DEPTH: u32 = 50;

/// Источник времени для `local_ts`. Трейт, а не прямой вызов часов — по той же
/// причине, что `ARCHITECTURE.md` A2 даёт этому пути (рекордер, без `Bot`
/// крейта) собственный `Clock`: там, где нет `Bot::current_timestamp()`, нужен
/// свой источник, и здесь он именно им и является. Единственная причина, по
/// которой это трейт, а не голый `SystemTime::now()`, — тесты: без него нельзя
/// было бы отличить «метка стоит до разбора» от «после», не устраивая гонку с
/// настоящими часами внутри теста.
pub trait Clock: Send {
    /// Наносекунды от эпохи Unix. Наносекунды — потому что `exch_ts` (`cts`/`T`
    /// биржи) идёт в миллисекундах, и сравнение `exch_ts <= local_ts` (шаг 0.5,
    /// GC) — это умножение на 10⁶ на стороне вызывающего кода, а не отдельная
    /// единица времени, которую каждый потребитель конвертирует по-своему.
    fn now_ns(&self) -> i64;
}

/// Системные часы. Единственная реализация, которую видит боевой код и,
/// поскольку сети в тестах нет, единственная, которую видят и тесты этого
/// файла — часы не сеть, дважды звонить бирже не нужно, чтобы их вызвать.
///
/// `SystemTime`, не `Instant`: `local_ts` обязан быть сравним с временем биржи
/// (эпоха Unix), а `Instant` — непрозрачная точка отсчёта без неё. Плата за
/// это в том, что `SystemTime` не гарантированно монотонны при скачке
/// NTP-коррекции — и это ровно то, что независимо и постоянно проверяет
/// `lob clock` (шаг 0.5: `|offset| < 5 мс`, без скачков `> 1 мс`), а не
/// свойство, которое обязан восстанавливать этот файл.
#[derive(Debug, Default, Clone, Copy)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now_ns(&self) -> i64 {
        Self::ns_since_epoch(SystemTime::now())
    }
}

impl SystemClock {
    /// Вынесено из `now_ns` отдельной чистой функцией ровно ради тестируемости
    /// отката: настоящие часы хоста «раньше эпохи» не устроить, а `SystemTime`
    /// раньше эпохи сконструировать можно (`UNIX_EPOCH - Duration::from_secs(..)`)
    /// — тест дёргает эту функцию таким временем напрямую, не полагаясь на
    /// сломанные часы настоящего хоста.
    ///
    /// Раньше здесь стоял `.expect(...)`: мёртвая RTC или несконфигурированная
    /// VM отдают `SystemTime::now() < UNIX_EPOCH`, и это падало на первом же
    /// фрейме — рекордер, рассчитанный на недели без присмотра, гас в первую
    /// же секунду работы. Панике здесь взяться неоткуда: `Err` несёт `Duration`
    /// ровно той величины, на которую часы отстают от эпохи, и это превращается
    /// в отрицательную метку, а не в аварийную остановку. Число будет заведомо
    /// неверным, но обнаружить и сообщить о сломанных часах хоста — работа
    /// `lob clock` (шаг 0.5, `ARCHITECTURE.md`), а не горячего пути `recv`.
    fn ns_since_epoch(now: SystemTime) -> i64 {
        match now.duration_since(UNIX_EPOCH) {
            // `as_nanos()` — `u128`; для дат до 2262 года влезает в `i64` без
            // обрезки, а дальше этот код проживёт свою жизнь много раз.
            Ok(since_epoch) => since_epoch.as_nanos() as i64,
            Err(err) => -(err.duration().as_nanos() as i64),
        }
    }
}

/// Один фрейм транспорта. `Text` — единственный вид, который несёт протокол
/// Bybit; `Closed` — конец сессии, независимо от того, кто её закрыл, сервер
/// или сеть: вызывающему коду обе причины важны одинаково — сокет мёртв,
/// нужно переподключаться.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Frame {
    Text(String),
    Closed,
}

/// Ошибка транспорта. Единственный вариант, потому что вызывающему коду не
/// важна причина обрыва — таймаут, разрыв TCP, ошибка TLS — реакция на все них
/// одна: переподключение с бэкоффом.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransportError {
    Io(String),
}

/// Сокет за трейтом — единственное, что делает эту логику тестируемой без
/// сети (design constraint задачи). Продовая реализация — `WsTransport` поверх
/// `tokio-tungstenite`; в тестах — целиком в памяти сценарный `ScriptedTransport`.
///
/// Методы возвращают `impl Future<..> + Send`, а не `async fn`: обычный
/// `async fn` в трейте не даёт задать `+ Send` на возвращаемом future, а без
/// него `Connection::run` нельзя было бы передать в `tokio::spawn` (там это
/// нужно тестам — сессия крутится в фоне, пока тест читает из канала).
pub trait Transport: Send {
    fn send_text(&mut self, msg: String)
        -> impl Future<Output = Result<(), TransportError>> + Send;
    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send;
}

/// Открывает новый транспорт. Отдельно от `Transport`: переподключение создаёт
/// новый сокет, а не чинит старый — TCP-соединение, разорванное сетью, чинить
/// нечем, и попытка переиспользовать `Transport` после `Closed` не имеет
/// смысла ни для реального сокета, ни для сценарного.
pub trait TransportConnector: Send {
    type Transport: Transport;
    fn connect(&mut self) -> impl Future<Output = Result<Self::Transport, TransportError>> + Send;
}

/// Экспоненциальный бэкофф переподключения. Поля, не константы: интервал
/// переподключения не задан ни в `PLAN.md`, ни протоколом биржи — это наш
/// выбор эксплуатации, и план прямо запрещает изобретать числа в теле кода,
/// а не в параметре, который выставляет вызывающий код.
#[derive(Debug, Clone, Copy)]
pub struct BackoffConfig {
    pub initial: Duration,
    pub max: Duration,
    pub multiplier: u32,
}

impl BackoffConfig {
    /// Задержка перед попыткой номер `attempt` (считая с нуля). Через
    /// `checked_pow`/`checked_mul`, а не `*`: на долгой сессии число попыток
    /// не ограничено ничем, кроме времени, и обычное умножение рано или
    /// поздно переполнило бы `Duration` и запаниковало вместо того, чтобы
    /// просто упереться в `max`.
    fn delay_for_attempt(&self, attempt: u32) -> Duration {
        let scaled = self
            .multiplier
            .checked_pow(attempt)
            .and_then(|factor| self.initial.checked_mul(factor));
        match scaled {
            Some(d) if d < self.max => d,
            _ => self.max,
        }
    }
}

/// Конфигурация одного подключения. Каждое поле — то, что план не задаёт
/// числом и что этот файл не имеет права выдумать: символ и шаги цены/размера
/// приходят из отбора инструмента (шаг 0.4, `instruments.csv`), интервал
/// пинга — из требования протокола Bybit (не из `PLAN.md`), бэкофф — из
/// эксплуатационного решения вызывающего кода.
#[derive(Debug, Clone)]
pub struct ConnConfig {
    pub symbol: String,
    /// Шаг цены в 1e-9 — тот же масштаб, что `book::Book::new` уже требует.
    pub tick_e9: i64,
    /// Шаг количества в 1e-9, тот же смысл.
    pub step_e9: i64,
    /// Bybit требует периодический `{"op":"ping"}`, иначе сервер рвёт
    /// соединение по своему таймауту (документированное поведение биржи,
    /// не число из `PLAN.md` — поэтому параметр, а не константа здесь).
    pub ping_interval: Duration,
    pub backoff: BackoffConfig,
}

/// Событие, которое соединение отдаёт наружу. Вызывающий код сам решает, что
/// с ним делать (собственную книгу вести, писать в бинлог, считать метрики) —
/// этот файл знает только протокол Bybit и сокет, а не то, что происходит с
/// событием дальше.
#[derive(Debug, Clone, PartialEq)]
pub enum ConnEvent {
    /// Одно событие потока, `local_ts_ns` — метка, поставленная до разбора.
    Message { local_ts_ns: i64, event: Event },
    /// Сырой фрейм не разобрался. Метка всё равно есть — она берётся до
    /// разбора и не зависит от его исхода; иначе этот вариант доказывал бы,
    /// что `local_ts` ставится после разбора, а не до.
    ParseFailed {
        local_ts_ns: i64,
        err: ws::ParseError,
    },
    /// `book::Book::apply` вернул `SequenceGap`: пропущенное сообщение, книга
    /// внутри этого файла больше не доверяется. Соединение уже отправило
    /// повторную подписку на `orderbook.50` и ждёт свежий снапшот; дельты
    /// между разрывом и снапшотом наружу не идут вовсе (см. `handle_raw`).
    SequenceGap { expected: u64, got: u64 },
    /// `book::Book::apply` вернул `PriceNotOnTick`, `QtyNotOnStep` или `Crossed` —
    /// любую ошибку `ApplyError`, кроме `SequenceGap`. Реакция внутри файла та
    /// же: ресинк снапшотом (см. `handle_raw`), но раньше эти три варианта не
    /// долетали наружу вовсе — наружу шёл только `SequenceGap`, а `Crossed`
    /// (книга гарантированно испорчена) и `PriceNotOnTick` (смена тик-сайза
    /// биржи посреди записи, `PLAN.md` шаг 0.3) были не видны никому, кто
    /// слушает только `out`. Несёт саму ошибку, а не только текст лога: адресату
    /// нужны числа (`price_e9`/`tick_e9` и т.д.), чтобы отличить смену тика от
    /// пересечения книги, а не только факт «что-то не так».
    BookInvariantViolated { err: ApplyError },
    /// Сокет закрылся (сервером или сетью). Переподключение уже запущено.
    Disconnected,
}

/// Формат пинга задан протоколом Bybit, а не `PLAN.md`: `{"op":"ping"}` без
/// топика. Не в `ws.rs` — тот файл не мой, и там это было бы разбором
/// входящего, а это исходящее сообщение транспорта, как `sub_orderbook` и
/// `sub_trades`, только для них место уже занято в чужом файле.
fn ping_message() -> String {
    r#"{"op":"ping"}"#.to_string()
}

/// Состояние одной сессии сокета: книга, флаг ресинка и флаг «сессия уже
/// переслала хоть одно сообщение». Собраны в одну структуру и переданы в
/// `handle_raw` одним аргументом — не ради красоты, а потому что порознь
/// список аргументов `handle_raw` перевалил бы за предел, который держит
/// clippy (`too_many_arguments`): все три поля описывают состояние ОДНОЙ
/// сессии одного сокета, а не что-то концептуально разное, так что это ещё и
/// более честная группировка, а не обход линта.
struct Session {
    book: Book,
    /// Не даёт слать повторную ресинк-подписку на каждую следующую дельту,
    /// пока мы уже ждём снапшот — иначе разрыв на секунду превратился бы в
    /// шторм из сотен подписок в секунду.
    resyncing: bool,
    /// Ставится в `true`, как только сессия переслала наружу хоть одно
    /// `ConnEvent::Message`. Решает судьбу счётчика бэкоффа в `run` — сервер,
    /// который принимает рукопожатие и тут же рвёт соединение, не должен
    /// казаться «здоровым» одним лишь фактом успешной подписки.
    productive: bool,
}

/// Одно подключение: символ, состояние ресинка и сокет за `TransportConnector`.
pub struct Connection<C: TransportConnector> {
    connector: C,
    cfg: ConnConfig,
}

impl<C: TransportConnector> Connection<C> {
    pub fn new(connector: C, cfg: ConnConfig) -> Self {
        Self { connector, cfg }
    }

    /// Основной цикл: подключение → две подписки → чтение с пересылкой в
    /// `out`, пока сокет жив → бэкофф и заново, если нет. Не возвращается сам
    /// по себе — остановка снаружи: оборвать `JoinHandle` (как в тестах) или
    /// закрыть `out`, после чего `send` начнёт возвращать ошибку, которую этот
    /// цикл молча игнорирует, а не паникует на ней (получатель мог закрыться
    /// осознанно, это не повод ронять поток ввода-вывода).
    pub async fn run(mut self, clock: impl Clock + 'static, out: mpsc::Sender<ConnEvent>) {
        let mut attempt: u32 = 0;
        loop {
            let mut transport = match self.connector.connect().await {
                Ok(t) => t,
                Err(_) => {
                    tokio::time::sleep(self.cfg.backoff.delay_for_attempt(attempt)).await;
                    attempt = attempt.saturating_add(1);
                    continue;
                }
            };

            // Новый сокет — новая последовательность `u` с нуля. Прошлая книга,
            // флаг ресинка и флаг «сессия была полезной» относятся к прошлой
            // сессии и не несут смысла здесь: старое состояние иначе выглядело
            // бы как разрыв в первом же сообщении новой сессии, хотя это
            // просто новое начало. Три поля собраны в одну структуру, а не
            // переданы в `handle_raw` по отдельности — иначе список аргументов
            // перевалил бы за предел, который держит clippy (`too_many_arguments`),
            // ровно тем сигналом, который эта структура и объединяет: все три
            // описывают состояние ОДНОЙ сессии, а не что-то из разных миров.
            let mut session = Session {
                book: Book::new(self.cfg.tick_e9, self.cfg.step_e9),
                resyncing: false,
                productive: false,
            };

            let subscribed = transport
                .send_text(ws::sub_orderbook(ORDERBOOK_DEPTH, &self.cfg.symbol))
                .await
                .is_ok()
                && transport
                    .send_text(ws::sub_trades(&self.cfg.symbol))
                    .await
                    .is_ok();
            if !subscribed {
                tokio::time::sleep(self.cfg.backoff.delay_for_attempt(attempt)).await;
                attempt = attempt.saturating_add(1);
                continue;
            }
            // Бэкофф здесь больше НЕ сбрасывается. Сброс сразу по успешной
            // подписке проверял только рукопожатие, а не сессию: сервер,
            // который принимает подписку и тут же рвёт соединение — рейт-лимит,
            // сброс нагрузки, дефектный прокси — заставлял бы цикл держаться
            // пола бэкоффа вечно, повторно долбя один и тот же адрес на
            // максимальной частоте. Признак «сессия была полезной» —
            // `session.productive`; сброс происходит только после него, когда
            // сессия уже закончилась.

            let mut ping_due = tokio::time::interval(self.cfg.ping_interval);
            // Missed-тики копятся по умолчанию и стреляют очередью один за
            // другим при первой возможности; `Delay` вместо этого просто
            // сдвигает следующий тик, что и нужно для пинга — частый залп не
            // держит соединение живее одного вовремя отправленного.
            ping_due.set_missed_tick_behavior(MissedTickBehavior::Delay);
            // `interval` тикает немедленно при создании; без этого пинг ушёл
            // бы сразу вслед за подпиской, до всякого интервала.
            ping_due.tick().await;

            loop {
                tokio::select! {
                    frame = transport.recv() => {
                        match frame {
                            Ok(Frame::Text(raw)) => {
                                // Метка ставится здесь и нигде позже — до
                                // единственного вызова `parse_message` ниже.
                                // Это и есть `H12`: точка "сразу после recv".
                                let local_ts_ns = clock.now_ns();
                                let alive = Self::handle_raw(
                                    &raw,
                                    local_ts_ns,
                                    &mut session,
                                    &self.cfg.symbol,
                                    &mut transport,
                                    &out,
                                )
                                .await;
                                if !alive {
                                    break;
                                }
                            }
                            Ok(Frame::Closed) | Err(_) => {
                                let _ = out.send(ConnEvent::Disconnected).await;
                                break;
                            }
                        }
                    }
                    _ = ping_due.tick() => {
                        if transport.send_text(ping_message()).await.is_err() {
                            let _ = out.send(ConnEvent::Disconnected).await;
                            break;
                        }
                    }
                }
            }

            // Сессия закончилась — здесь и только здесь решаем судьбу счётчика.
            // `session.productive` ставится в `handle_raw` при первой же
            // пересланной `Message`: если хоть одна дошла, соединение доказало,
            // что живёт не одним рукопожатием, и счётчик обнуляется — быстрый
            // реконнект после нормальной работы не должен наказываться прошлым
            // бэкоффом. Если нет — соединение росло от предыдущего значения
            // `attempt` (а не с нуля, как раньше), и задержка следующей попытки
            // растёт, а не топчется на полу. Порог «пересланное сообщение», а
            // не «сессия прожила N секунд»: он не изобретает время эксплуатации
            // (которого план не задаёт) и не требует своих часов внутри цикла
            // подключения — здесь уже есть ровно то состояние, что нужно.
            if session.productive {
                attempt = 0;
            } else {
                attempt = attempt.saturating_add(1);
            }
            tokio::time::sleep(self.cfg.backoff.delay_for_attempt(attempt)).await;
        }
    }

    /// Разбирает один сырой фрейм, применяет обновления книги к локальной
    /// книге сессии и решает, пересылать ли событие наружу. Возвращает
    /// `false`, если сессию нужно закрывать (отправка резинка в уже мёртвый
    /// сокет не удалась).
    async fn handle_raw(
        raw: &str,
        local_ts_ns: i64,
        session: &mut Session,
        symbol: &str,
        transport: &mut C::Transport,
        out: &mpsc::Sender<ConnEvent>,
    ) -> bool {
        let events = match ws::parse_message(raw) {
            Ok(evs) => evs,
            Err(err) => {
                let _ = out.send(ConnEvent::ParseFailed { local_ts_ns, err }).await;
                return true;
            }
        };

        for event in events {
            if let Event::Book(update) = &event {
                match session.book.apply(update) {
                    Ok(()) => {
                        session.resyncing = false;
                    }
                    Err(err) => {
                        // Любая ошибка `apply` — не только `SequenceGap` — по
                        // контракту `book::ApplyError` означает «книга больше
                        // не доверена» (см. комментарий на самом типе в
                        // `book/mod.rs`). Реакция одна и та же: ресинк
                        // снапшотом. Флаг `resyncing` не даёт слать повторную
                        // подписку на каждую следующую дельту, пока мы уже
                        // ждём снапшот — иначе разрыв на секунду превратился
                        // бы в шторм из сотен подписок в секунду.
                        if !session.resyncing {
                            session.resyncing = true;
                            // `SequenceGap` несёт свои `expected`/`got` в
                            // отдельном варианте `ConnEvent` уже давно; три
                            // остальных варианта `ApplyError` раньше здесь не
                            // упоминались вовсе — ветка ловила только разрыв
                            // `u`, а `PriceNotOnTick`/`QtyNotOnStep`/`Crossed`
                            // проваливались в тот же ресинк молча, без единого
                            // события наружу. `Crossed` — уже точно испорченная
                            // книга, `PriceNotOnTick` — ровно то, чем план (шаг
                            // 0.3) обнаруживает смену тик-сайза биржи; оба не
                            // имеют права быть невидимыми снаружи этого файла.
                            match &err {
                                ApplyError::SequenceGap { expected, got } => {
                                    let _ = out
                                        .send(ConnEvent::SequenceGap {
                                            expected: *expected,
                                            got: *got,
                                        })
                                        .await;
                                }
                                _ => {
                                    let _ = out
                                        .send(ConnEvent::BookInvariantViolated { err: err.clone() })
                                        .await;
                                }
                            }
                            if transport
                                .send_text(ws::sub_orderbook(ORDERBOOK_DEPTH, symbol))
                                .await
                                .is_err()
                            {
                                // Три способа умереть внутри select! в `run`
                                // обязаны выглядеть одинаково снаружи: закрытый
                                // фрейм и неудавшийся пинг уже шлют `Disconnected`
                                // перед тем, как разорвать цикл. Эта отправка —
                                // тоже путь смерти сокета, просто обнаруженный
                                // на один вызов глубже (внутри ресинка, а не в
                                // самом `select!`); без события здесь вызывающий
                                // код, размечающий границы сессии по
                                // `Disconnected`, одну из трёх смертей не увидит
                                // никогда — раньше именно так и было.
                                let _ = out.send(ConnEvent::Disconnected).await;
                                return false;
                            }
                        }
                        // Не пересылаем: книга с этим событием не совпадает ни
                        // с чем, чему можно доверять, а «продолжить как ни в
                        // чём не бывало» — ровно то поведение, которое план
                        // запрещает прямым текстом.
                        continue;
                    }
                }
            }
            // Хоть одно сообщение дошло до пересылки — сессия доказала, что
            // живёт не одним только рукопожатием подписки. Это и есть признак
            // «полезной» сессии для сброса бэкоффа в `run` (см. комментарий
            // там); ставится безусловно, независимо от того, успеет ли `out`
            // принять сообщение — сам факт, что было что переслать, уже
            // случился и не зависит от состояния канала на другом конце.
            session.productive = true;
            let _ = out.send(ConnEvent::Message { local_ts_ns, event }).await;
        }
        true
    }
}

/// Публичный линейный поток Bybit v5. Адрес — факт протокола биржи (как имена
/// топиков в `ws.rs`), а не измеренное число и не порог: запрет изобретённых
/// констант — про них, не про адреса.
pub const PUBLIC_LINEAR_URL: &str = "wss://stream.bybit.com/v5/public/linear";

/// Реальный сокет: `tokio-tungstenite` за тем же трейтом, что и сценарный
/// транспорт тестов. Сеть в тестах этого файла не участвует — этот тип
/// проверяется тем, что компилируется и что он единственная реализация
/// `Transport`, которая ходит в сеть, а не отдельным сетевым тестом.
pub struct WsTransport {
    stream: tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
}

impl Transport for WsTransport {
    fn send_text(
        &mut self,
        msg: String,
    ) -> impl Future<Output = Result<(), TransportError>> + Send {
        use futures::SinkExt;
        async move {
            self.stream
                .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                .await
                .map_err(|e| TransportError::Io(e.to_string()))
        }
    }

    fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
        use futures::StreamExt;
        use tokio_tungstenite::tungstenite::Message as TungsteniteMessage;
        async move {
            loop {
                match self.stream.next().await {
                    Some(Ok(TungsteniteMessage::Text(text))) => return Ok(Frame::Text(text)),
                    Some(Ok(TungsteniteMessage::Close(_))) | None => return Ok(Frame::Closed),
                    // Ping/Pong/Binary/сырой Frame — не протокол Bybit поверх
                    // этого канала; WS-пинг `tungstenite` обслуживает сам,
                    // здесь их достаточно пропустить и ждать следующий фрейм.
                    Some(Ok(_)) => continue,
                    Some(Err(e)) => return Err(TransportError::Io(e.to_string())),
                }
            }
        }
    }
}

/// Коннектор публичного линейного потока. Один на процесс: каждый `connect`
/// открывает новый TCP+TLS+WS с нуля, потому что переподключение и есть новая
/// сессия сервера, а не восстановление старой (Bybit не хранит состояние
/// подписки между разрывами).
#[derive(Debug, Default, Clone, Copy)]
pub struct BybitPublicLinearConnector;

impl TransportConnector for BybitPublicLinearConnector {
    type Transport = WsTransport;

    async fn connect(&mut self) -> Result<WsTransport, TransportError> {
        let (stream, _response) = tokio_tungstenite::connect_async(PUBLIC_LINEAR_URL)
            .await
            .map_err(|e| TransportError::Io(e.to_string()))?;
        Ok(WsTransport { stream })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    /// Транспорт по сценарию: очередь фреймов на приём плюс лог того, что
    /// через него отправили. Единственная замена сети во всех тестах файла.
    ///
    /// Пустая очередь — это НЕ конец сессии: `recv` зависает (`pending`),
    /// как настоящий сокет, у которого просто пока нет данных. Закрытие
    /// сессии — это явный `Ok(Frame::Closed)` в очереди, а не её исчерпание;
    /// иначе тест на пинг никогда не дождался бы своего интервала — первый же
    /// `recv` после последнего заскриптованного фрейма "закрывал" бы сессию.
    struct ScriptedTransport {
        inbox: VecDeque<Result<Frame, TransportError>>,
        sent: Arc<Mutex<Vec<String>>>,
    }

    impl Transport for ScriptedTransport {
        fn send_text(
            &mut self,
            msg: String,
        ) -> impl Future<Output = Result<(), TransportError>> + Send {
            let sent = self.sent.clone();
            async move {
                sent.lock().unwrap().push(msg);
                Ok(())
            }
        }

        fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
            let next = self.inbox.pop_front();
            async move {
                match next {
                    Some(item) => item,
                    None => std::future::pending().await,
                }
            }
        }
    }

    /// Выдаёт по одному транспорту на подключение — так переподключение в
    /// тесте наблюдаемо как новая сессия со своей очередью фреймов, а не
    /// продолжение старой.
    struct ScriptedConnector {
        sessions: VecDeque<VecDeque<Result<Frame, TransportError>>>,
        sent: Arc<Mutex<Vec<String>>>,
    }

    impl ScriptedConnector {
        fn new(
            sessions: Vec<Vec<Result<Frame, TransportError>>>,
        ) -> (Self, Arc<Mutex<Vec<String>>>) {
            let sent = Arc::new(Mutex::new(Vec::new()));
            let sessions = sessions.into_iter().map(VecDeque::from).collect();
            (
                Self {
                    sessions,
                    sent: sent.clone(),
                },
                sent,
            )
        }
    }

    impl TransportConnector for ScriptedConnector {
        type Transport = ScriptedTransport;

        fn connect(
            &mut self,
        ) -> impl Future<Output = Result<ScriptedTransport, TransportError>> + Send {
            let next = self.sessions.pop_front();
            let sent = self.sent.clone();
            async move {
                match next {
                    Some(inbox) => Ok(ScriptedTransport { inbox, sent }),
                    None => Err(TransportError::Io(
                        "сценарий подключений исчерпан".to_string(),
                    )),
                }
            }
        }
    }

    /// Транспорт, у которого проваливается ровно один по счёту вызов
    /// `send_text` (1-based `fail_on_call`), остальные и `recv` ведут себя как
    /// обычный сценарный транспорт. Нужен ровно для FIX 1: до этого двойника
    /// ни один тест не мог провалить именно ресинковскую отправку — обычный
    /// `ScriptedTransport::send_text` всегда возвращает `Ok`, — и путь «отправка
    /// ресинка не удалась» в `handle_raw` был непроверяем в принципе.
    struct SendFailsOnCall {
        inbox: VecDeque<Result<Frame, TransportError>>,
        send_calls: usize,
        fail_on_call: usize,
    }

    impl Transport for SendFailsOnCall {
        fn send_text(
            &mut self,
            _msg: String,
        ) -> impl Future<Output = Result<(), TransportError>> + Send {
            self.send_calls += 1;
            let should_fail = self.send_calls == self.fail_on_call;
            async move {
                if should_fail {
                    Err(TransportError::Io(
                        "сценарий: эта конкретная отправка проваливается намеренно".to_string(),
                    ))
                } else {
                    Ok(())
                }
            }
        }

        fn recv(&mut self) -> impl Future<Output = Result<Frame, TransportError>> + Send {
            let next = self.inbox.pop_front();
            async move {
                match next {
                    Some(item) => item,
                    None => std::future::pending().await,
                }
            }
        }
    }

    /// Отдаёт один-единственный заранее собранный транспорт, затем считает
    /// сценарий исчерпанным. Смысл существования — обвязка для
    /// `SendFailsOnCall`, которому (в отличие от `ScriptedConnector`) сессия
    /// нужна ровно одна и с точным счётчиком отправок, а не очередь сессий.
    struct SingleTransportConnector {
        transport: Option<SendFailsOnCall>,
    }

    impl TransportConnector for SingleTransportConnector {
        type Transport = SendFailsOnCall;

        fn connect(
            &mut self,
        ) -> impl Future<Output = Result<SendFailsOnCall, TransportError>> + Send {
            let next = self.transport.take();
            async move {
                match next {
                    Some(t) => Ok(t),
                    None => Err(TransportError::Io(
                        "единственная сессия уже выдана".to_string(),
                    )),
                }
            }
        }
    }

    /// Коннектор, который на каждый `connect()` выдаёт новый транспорт,
    /// закрывающийся немедленно (`Frame::Closed`, ни одного текстового кадра —
    /// то самое "принял рукопожатие и тут же разорвал", которое проверяет
    /// FIX 2), и запоминает момент вызова. Session-по-сценарию (как у
    /// `ScriptedConnector`) здесь не годится: конечная очередь рано или
    /// поздно кончается, и `connect()` начинает возвращать `Err` — путь,
    /// который был корректен и ДО FIX 2 (растущий бэкофф на неудачном
    /// коннекте никогда не был багом), и он замаскировал бы дефект, который
    /// тест обязан ловить, — бэкофф выглядел бы растущим по совершенно другой,
    /// не относящейся к делу причине. Поэтому сессии здесь не кончаются
    /// никогда. Настоящие часы (`std::time::Instant`), а не пауза
    /// `tokio::time` — фича `test-util` тестам этого файла не подключена
    /// (файл не мой, доступ к `Cargo.toml` не мой), и не стоит того, чтобы
    /// её просить ради одного теста.
    struct AlwaysCloseImmediatelyConnector {
        connect_times: Arc<Mutex<Vec<std::time::Instant>>>,
    }

    impl AlwaysCloseImmediatelyConnector {
        fn new() -> (Self, Arc<Mutex<Vec<std::time::Instant>>>) {
            let connect_times = Arc::new(Mutex::new(Vec::new()));
            (
                Self {
                    connect_times: connect_times.clone(),
                },
                connect_times,
            )
        }
    }

    impl TransportConnector for AlwaysCloseImmediatelyConnector {
        type Transport = ScriptedTransport;

        fn connect(
            &mut self,
        ) -> impl Future<Output = Result<ScriptedTransport, TransportError>> + Send {
            let connect_times = self.connect_times.clone();
            async move {
                connect_times
                    .lock()
                    .unwrap()
                    .push(std::time::Instant::now());
                Ok(ScriptedTransport {
                    inbox: VecDeque::from(vec![Ok(Frame::Closed)]),
                    sent: Arc::new(Mutex::new(Vec::new())),
                })
            }
        }
    }

    /// Часы, возвращающие строго возрастающий счётчик вместо времени. Нужны
    /// FIX 4: монотонность, которую проверяли старые тесты (`<=`), проходит и
    /// на константе — счётчик же гарантирует, что каждое отдельное показание
    /// уникально и по порядку, а число вызовов `now_ns()` совпадает с числом
    /// завершившихся `recv()`, ровно то свойство, которое обязано пережить
    /// будущий рефакторинг "меньше системных вызовов".
    #[derive(Clone)]
    struct CountingClock {
        calls: Arc<std::sync::atomic::AtomicI64>,
    }

    impl CountingClock {
        fn new() -> (Self, Arc<std::sync::atomic::AtomicI64>) {
            let calls = Arc::new(std::sync::atomic::AtomicI64::new(0));
            (
                Self {
                    calls: calls.clone(),
                },
                calls,
            )
        }
    }

    impl Clock for CountingClock {
        fn now_ns(&self) -> i64 {
            self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
        }
    }

    /// Бэкофф в тестах — миллисекунды, не секунды: тесты не про таймауты сети,
    /// а про то, что происходит после переподключения, и ждать реальную
    /// продовую задержку здесь незачем.
    fn test_cfg(symbol: &str) -> ConnConfig {
        ConnConfig {
            symbol: symbol.to_string(),
            tick_e9: 100_000,   // 0.0001, тот же масштаб, что в тестах book/mod.rs
            step_e9: 1_000_000, // 0.001
            ping_interval: Duration::from_secs(3600), // вне фокуса большинства тестов
            backoff: BackoffConfig {
                initial: Duration::from_millis(1),
                max: Duration::from_millis(5),
                multiplier: 2,
            },
        }
    }

    fn orderbook_msg(
        kind: &str,
        u: u64,
        cts_ms: i64,
        bids: &[(f64, f64)],
        asks: &[(f64, f64)],
    ) -> String {
        let render = |levels: &[(f64, f64)]| -> String {
            levels
                .iter()
                .map(|&(p, q)| format!(r#"["{p}","{q}"]"#))
                .collect::<Vec<_>>()
                .join(",")
        };
        format!(
            r#"{{"topic":"orderbook.50.SOLUSDT","type":"{kind}","ts":{cts_ms},"data":{{"b":[{}],"a":[{}],"u":{u}}},"cts":{cts_ms}}}"#,
            render(bids),
            render(asks)
        )
    }

    fn trade_msg(exch_ms: i64) -> String {
        format!(
            r#"{{"topic":"publicTrade.SOLUSDT","type":"snapshot","ts":1,"data":[{{"T":{exch_ms},"s":"SOLUSDT","S":"Buy","v":"1.0","p":"150.00","L":"PlusTick","i":"x","BT":false}}]}}"#
        )
    }

    /// Ждёт ровно `n` событий с таймаутом: без него баг в реализации вешает
    /// не один тест, а всю команду `cargo test` до ручной остановки.
    async fn collect_n(rx: &mut mpsc::Receiver<ConnEvent>, n: usize) -> Vec<ConnEvent> {
        let mut out = Vec::with_capacity(n);
        for _ in 0..n {
            let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
                .await
                .expect("не дождались события за 5с — соединение зависло")
                .expect("канал закрылся раньше, чем пришли все ожидаемые события");
            out.push(ev);
        }
        out
    }

    #[tokio::test]
    async fn local_ts_is_stamped_before_parse_and_is_monotonic_across_messages() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            // Не JSON вовсе: разбор обязан упасть, метка — нет.
            Ok(Frame::Text("not json at all".to_string())),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                2,
                &[(1.0, 6.0)],
                &[],
            ))),
        ];
        let (connector, _sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        let events = collect_n(&mut rx, 3).await; // 2 Message + 1 ParseFailed
        handle.abort();

        let mut timestamps = Vec::new();
        for ev in &events {
            match ev {
                ConnEvent::Message { local_ts_ns, .. } => timestamps.push(*local_ts_ns),
                ConnEvent::ParseFailed { local_ts_ns, .. } => timestamps.push(*local_ts_ns),
                other => panic!("неожиданное событие: {other:?}"),
            }
        }
        assert_eq!(
            timestamps.len(),
            3,
            "метка обязана стоять и на нераспарсенном фрейме — иначе она берётся после разбора"
        );
        assert!(
            timestamps.windows(2).all(|w| w[0] <= w[1]),
            "local_ts обязана быть монотонна: {timestamps:?}"
        );
    }

    #[tokio::test]
    async fn exch_ts_never_exceeds_local_ts_for_any_forwarded_message() {
        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                now_ms - 5,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            Ok(Frame::Text(trade_msg(now_ms - 3))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                now_ms - 1,
                &[(1.0, 6.0)],
                &[],
            ))),
        ];
        let (connector, _sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        let events = collect_n(&mut rx, 3).await;
        handle.abort();

        for ev in events {
            match ev {
                ConnEvent::Message { local_ts_ns, event } => {
                    let exch_ts_ns = match event {
                        Event::Book(u) => u.cts_ms * 1_000_000,
                        Event::Trade(t) => t.exch_ms * 1_000_000,
                        Event::Other => continue,
                    };
                    assert!(
                        exch_ts_ns <= local_ts_ns,
                        "exch_ts {exch_ts_ns} > local_ts {local_ts_ns}"
                    );
                }
                other => panic!("ожидалось Message, получено {other:?}"),
            }
        }
    }

    #[tokio::test]
    async fn sequence_gap_triggers_resubscribe_and_wait_for_resnapshot_not_silent_continuation() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))), // u=10, ok
            Ok(Frame::Text(orderbook_msg(
                "delta",
                12,
                2,
                &[(1.0, 6.0)],
                &[],
            ))), // разрыв: ждали 11, пришло 12
            Ok(Frame::Text(orderbook_msg(
                "delta",
                13,
                3,
                &[(1.0, 7.0)],
                &[],
            ))), // всё ещё в разрыве — повторной подписки быть не должно
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                20,
                4,
                &[(2.0, 1.0)],
                &[(2.0001, 1.0)],
            ))), // ресинк — поток возобновляется
        ];
        let (connector, sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        // Ровно три события: снапшот u=10, разрыв, ресинк-снапшот u=20.
        // Обе гэпнутые дельты (u=12, u=13) не порождают ни Message, ни
        // повторного SequenceGap — это и есть "не продолжать молча".
        let events = collect_n(&mut rx, 3).await;
        handle.abort();

        match &events[0] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 10),
            other => panic!("ожидался снапшот u=10, получено {other:?}"),
        }
        assert_eq!(
            events[1],
            ConnEvent::SequenceGap {
                expected: 11,
                got: 12
            }
        );
        match &events[2] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 20),
            other => panic!("ожидался ресинк-снапшот u=20, получено {other:?}"),
        }

        let sent = sent.lock().unwrap();
        let resubscribes = sent
            .iter()
            .filter(|m| m.contains("orderbook.50.SOLUSDT"))
            .count();
        assert_eq!(
            resubscribes, 2,
            "изначальная подписка плюс ровно один ресинк — не по подписке на каждую гэпнутую дельту"
        );
    }

    #[tokio::test]
    async fn u_equals_one_mid_stream_is_delegated_to_book_as_a_restart_not_a_gap() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                2,
                &[(1.0, 6.0)],
                &[],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                1, // рестарт сервиса Bybit
                3,
                &[(0.5, 2.0)],
                &[(0.5001, 2.0)],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                2, // продолжение уже от u=1 — валидно, только если рестарт принят
                4,
                &[(0.5, 3.0)],
                &[],
            ))),
        ];
        let (connector, sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        let events = collect_n(&mut rx, 4).await;
        handle.abort();

        // Все четыре — Message, ни одного SequenceGap: рестарт не ошибка.
        // Четвёртое событие (u=2) проходит только если `Book` приняла u=1 как
        // новую точку отсчёта — иначе оно само оказалось бы разрывом.
        for (i, ev) in events.iter().enumerate() {
            assert!(
                matches!(ev, ConnEvent::Message { .. }),
                "событие {i} не Message: {ev:?}"
            );
        }
        match &events[2] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 1),
            other => panic!("{other:?}"),
        }
        match &events[3] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 2),
            other => panic!("{other:?}"),
        }

        let sent = sent.lock().unwrap();
        let resubscribes = sent
            .iter()
            .filter(|m| m.contains("orderbook.50.SOLUSDT"))
            .count();
        assert_eq!(
            resubscribes, 1,
            "рестарт сервиса не должен вызывать ресинк по сети — это не разрыв последовательности"
        );
    }

    #[tokio::test]
    async fn socket_close_triggers_reconnect_that_resumes_and_resubscribes() {
        let session1 = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            Ok(Frame::Closed),
        ];
        let session2 = vec![Ok(Frame::Text(orderbook_msg(
            "snapshot",
            5,
            1,
            &[(3.0, 1.0)],
            &[(3.0001, 1.0)],
        )))];
        let (connector, sent) = ScriptedConnector::new(vec![session1, session2]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        // Message(u=10) → Disconnected → Message(u=5, уже вторая сессия).
        let events = collect_n(&mut rx, 3).await;
        handle.abort();

        match &events[0] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 10),
            other => panic!("{other:?}"),
        }
        assert_eq!(events[1], ConnEvent::Disconnected);
        match &events[2] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 5),
            other => panic!("{other:?}"),
        }

        let sent = sent.lock().unwrap();
        let subscribes = sent
            .iter()
            .filter(|m| m.contains("orderbook.50.SOLUSDT"))
            .count();
        assert_eq!(
            subscribes, 2,
            "переподключение обязано подписаться заново, а не переиспользовать старую подписку"
        );
    }

    /// Не входит в обязательный список задачи, но "пинг на интервале, который
    /// требует Bybit" — отдельное требование шага 0.1, и без этого теста его
    /// никто бы не проверял вовсе.
    #[tokio::test]
    async fn ping_is_sent_repeatedly_on_the_configured_interval() {
        let mut cfg = test_cfg("SOLUSDT");
        cfg.ping_interval = Duration::from_millis(5);
        let frames = vec![Ok(Frame::Text(orderbook_msg(
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        )))];
        let (connector, sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

        let _ = collect_n(&mut rx, 1).await; // снапшот получен, дальше сокет молчит
        tokio::time::sleep(Duration::from_millis(100)).await;
        handle.abort();

        let sent = sent.lock().unwrap();
        let pings = sent.iter().filter(|m| m.contains(r#""op":"ping""#)).count();
        assert!(
            pings >= 1,
            "за 100 мс при интервале 5 мс обязан пройти хотя бы один пинг, отправлено {pings}"
        );
    }

    /// FIX 1. Без `SendFailsOnCall` этот путь непроверяем: `ScriptedTransport`
    /// не умеет проваливать конкретную отправку, поэтому раньше `handle_raw`
    /// мог молча вернуть `false` на неудавшемся ресинке, и ни один тест этого
    /// не замечал. Счёт вызовов `send_text`: 1 — `sub_orderbook` при входе в
    /// сессию, 2 — `sub_trades`, 3 — повторная `sub_orderbook` на ресинке после
    /// разрыва `u`; проваливаем именно третий.
    #[tokio::test]
    async fn resync_resubscribe_send_failure_still_emits_disconnected() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                12, // разрыв: ждали 11 — запускает ресинк-подписку
                2,
                &[(1.0, 6.0)],
                &[],
            ))),
        ];
        let connector = SingleTransportConnector {
            transport: Some(SendFailsOnCall {
                inbox: VecDeque::from(frames),
                send_calls: 0,
                fail_on_call: 3,
            }),
        };
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        // Message(u=10) → SequenceGap → Disconnected. Без FIX 1 третье событие
        // никогда не приходит, и `collect_n` падает по таймауту 5с, а не по
        // неверному значению — баг вешает тест, а не просто заваливает assert.
        let events = collect_n(&mut rx, 3).await;
        handle.abort();

        match &events[0] {
            ConnEvent::Message {
                event: Event::Book(u),
                ..
            } => assert_eq!(u.u, 10),
            other => panic!("{other:?}"),
        }
        assert_eq!(
            events[1],
            ConnEvent::SequenceGap {
                expected: 11,
                got: 12
            }
        );
        assert_eq!(
            events[2],
            ConnEvent::Disconnected,
            "неудавшаяся отправка ресинка — тоже смерть сокета и обязана дать Disconnected, \
             как и два других пути в select!"
        );
    }

    /// FIX 2. Сервер, который принимает рукопожатие и тут же рвёт соединение
    /// (рейт-лимит, сброс нагрузки, дефектный прокси), не должен держать цикл
    /// на полу бэкоффа вечно. Ни одна из шести сессий не пересылает ни одного
    /// сообщения (только мгновенный `Frame::Closed`), поэтому счётчик попыток
    /// обязан расти от раунда к раунду, а не сбрасываться по одному лишь факту
    /// успешной подписки. Настоящие часы, не пауза `tokio::time` — см.
    /// комментарий на `TimestampingConnector`.
    ///
    /// Сравниваем не каждую пару соседних задержек (реальные часы под
    /// нагрузкой CI дают дребезг в районе потолка — 150мс иногда мерится как
    /// 145 или 175), а раннюю фазу роста против плато: первая задержка обязана
    /// быть у пола, последующие — заметно выше и обязаны ОСТАВАТЬСЯ там, а не
    /// проваливаться обратно к полу, как было бы при сбросе счётчика.
    #[tokio::test]
    async fn connect_then_close_without_forwarding_a_message_does_not_reset_backoff() {
        let mut cfg = test_cfg("SOLUSDT");
        cfg.backoff = BackoffConfig {
            initial: Duration::from_millis(3),
            max: Duration::from_millis(150),
            multiplier: 5,
        };
        let (connector, connect_times) = AlwaysCloseImmediatelyConnector::new();
        let (tx, _rx) = mpsc::channel(16);
        let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

        // Бюджет с большим запасом: при исправленном бэкоффе несколько раундов
        // укладываются в районе секунды (3+15+75+150+150+... мс плюс шедулинг),
        // при сломанном — счётчик каждый раз падает на пол в 3мс. Разница на
        // порядок, а не в разы, поэтому запас не даёт багу "тоже успеть". Сессии
        // здесь не кончаются никогда (см. `AlwaysCloseImmediatelyConnector`),
        // поэтому весь рост задержки объясняется только тем, что проверяет
        // этот тест, а не побочным путём "коннектор исчерпан".
        tokio::time::sleep(Duration::from_millis(1200)).await;
        handle.abort();

        let times = connect_times.lock().unwrap().clone();
        assert!(
            times.len() >= 5,
            "ожидались хотя бы 5 попыток подключения за 1.2с, получено {}",
            times.len()
        );
        let gaps: Vec<Duration> = times.windows(2).map(|w| w[1] - w[0]).collect();
        assert!(
            gaps[0] < Duration::from_millis(60),
            "первая задержка обязана быть у пола (~3мс): {gaps:?}"
        );
        assert!(
            gaps[gaps.len() - 3..]
                .iter()
                .all(|g| *g > Duration::from_millis(100)),
            "после нескольких непродуктивных раундов задержка обязана вырасти к потолку \
             (150мс) и остаться там, а не проваливаться обратно к полу (3мс) — это и значило \
             бы, что счётчик попыток сбрасывается без всякой причины: {gaps:?}"
        );
    }

    /// FIX 3. Раньше `SystemClock::now_ns` паниковал через `.expect(...)` на
    /// `SystemTime::now() < UNIX_EPOCH`; настоящие часы хоста в этот момент не
    /// вставить в тест, поэтому фолбэк проверяется на чистой функции
    /// `ns_since_epoch` с `SystemTime`, сконструированным раньше эпохи напрямую.
    #[test]
    fn system_clock_falls_back_instead_of_panicking_before_unix_epoch() {
        let before_epoch = UNIX_EPOCH - Duration::from_secs(1);
        let ns = SystemClock::ns_since_epoch(before_epoch);
        assert_eq!(
            ns, -1_000_000_000,
            "секунда до эпохи обязана дать -1e9 нс, а не панику"
        );

        // Обычный путь не сломан рефакторингом: секунда ПОСЛЕ эпохи по-прежнему
        // даёт положительные наносекунды.
        let after_epoch = UNIX_EPOCH + Duration::from_secs(1);
        assert_eq!(SystemClock::ns_since_epoch(after_epoch), 1_000_000_000);
    }

    /// FIX 4. Старые H12-тесты используют `SystemClock` и проверяют `<=` —
    /// свойство, которое не отличает "часы читаются на каждый кадр" от "часы
    /// прочитаны один раз на сессию и разошлись по всем событиям": константа
    /// тоже монотонна нестрого. `CountingClock` закрывает именно это: каждое
    /// показание уникально по построению, поэтому строгий рост доказывает, что
    /// `now_ns()` вызывается заново на каждый кадр, а не переиспользуется, а
    /// сверка счётчика с числом кадров доказывает, что он не вызывается и
    /// ЛИШНИЙ раз тоже (иначе значения росли бы шагом >1, но сам факт строгого
    /// роста этого не поймал бы — ловит только сверка со счётчиком фреймов).
    #[tokio::test]
    async fn local_ts_advances_exactly_once_per_completed_recv_and_is_strictly_increasing() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                2,
                &[(1.0, 6.0)],
                &[],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                12,
                3,
                &[(1.0, 7.0)],
                &[],
            ))),
            Ok(Frame::Text(orderbook_msg(
                "delta",
                13,
                4,
                &[(1.0, 8.0)],
                &[],
            ))),
        ];
        let n_frames = frames.len();
        let (connector, _sent) = ScriptedConnector::new(vec![frames]);
        let (clock, calls) = CountingClock::new();
        let (tx, mut rx) = mpsc::channel(16);
        let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(clock, tx));

        let events = collect_n(&mut rx, n_frames).await;
        handle.abort();

        let timestamps: Vec<i64> = events
            .iter()
            .map(|ev| match ev {
                ConnEvent::Message { local_ts_ns, .. } => *local_ts_ns,
                other => panic!("ожидался Message: {other:?}"),
            })
            .collect();
        assert!(
            timestamps.windows(2).all(|w| w[0] < w[1]),
            "метки обязаны СТРОГО расти кадр за кадром: {timestamps:?}"
        );
        assert_eq!(
            calls.load(std::sync::atomic::Ordering::SeqCst) as usize,
            n_frames,
            "часы обязаны читаться ровно один раз на каждый завершившийся recv — не реже \
             (одно чтение на сессию не прошло бы строгий рост) и не чаще"
        );
    }

    /// FIX 5. `PriceNotOnTick` — то, чем `PLAN.md` (шаг 0.3) детектирует смену
    /// тик-сайза биржи посреди записи; раньше эта ошибка `apply` глушилась в
    /// ресинке молча, без единого события наружу.
    #[tokio::test]
    async fn price_not_on_tick_is_observable_and_triggers_resync() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            // tick_e9 = 100_000 (0.0001) в test_cfg; "1.00005" даёт price_e9,
            // не кратный ему — ровно случай смены шага биржи посреди потока.
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                2,
                &[(1.00005, 6.0)],
                &[],
            ))),
        ];
        let (connector, sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        let events = collect_n(&mut rx, 2).await; // Message(u=10) → BookInvariantViolated
        handle.abort();

        match &events[1] {
            ConnEvent::BookInvariantViolated { err } => {
                assert!(
                    matches!(err, ApplyError::PriceNotOnTick { .. }),
                    "ожидался PriceNotOnTick, получено {err:?}"
                );
            }
            other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
        }
        let resubscribes = sent
            .lock()
            .unwrap()
            .iter()
            .filter(|m| m.contains("orderbook.50.SOLUSDT"))
            .count();
        assert_eq!(
            resubscribes, 2,
            "PriceNotOnTick обязан запускать тот же ресинк, что и SequenceGap"
        );
    }

    /// FIX 5. `QtyNotOnStep` — тот же контракт `ApplyError`, что и
    /// `PriceNotOnTick` (см. `book/mod.rs`), только по размеру, а не по цене.
    #[tokio::test]
    async fn qty_not_on_step_is_observable_and_triggers_resync() {
        let frames = vec![
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            // step_e9 = 1_000_000 (0.001) в test_cfg; "6.0001" не кратно ему.
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                2,
                &[(1.0, 6.0001)],
                &[],
            ))),
        ];
        let (connector, _sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        let events = collect_n(&mut rx, 2).await;
        handle.abort();

        match &events[1] {
            ConnEvent::BookInvariantViolated { err } => {
                assert!(
                    matches!(err, ApplyError::QtyNotOnStep { .. }),
                    "ожидался QtyNotOnStep, получено {err:?}"
                );
            }
            other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
        }
    }

    /// FIX 5. `Crossed` — операционно самый интересный случай: книга уже
    /// точно испорчена, а не просто временно рассинхронизирована разрывом `u`.
    /// Раньше был невидим наружу этого файла точно так же, как и два других.
    #[tokio::test]
    async fn crossed_book_is_observable_and_triggers_resync() {
        let frames = vec![
            // Спред в один тик: бид 1.0, аск 1.0001.
            Ok(Frame::Text(orderbook_msg(
                "snapshot",
                10,
                1,
                &[(1.0, 5.0)],
                &[(1.0001, 4.0)],
            ))),
            // Новый бид 1.0002 выше старого аска 1.0001 — книга пересекается
            // сразу после применения бидов, аски в этой дельте не трогаем.
            Ok(Frame::Text(orderbook_msg(
                "delta",
                11,
                2,
                &[(1.0002, 6.0)],
                &[],
            ))),
        ];
        let (connector, _sent) = ScriptedConnector::new(vec![frames]);
        let (tx, mut rx) = mpsc::channel(16);
        let handle =
            tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

        let events = collect_n(&mut rx, 2).await;
        handle.abort();

        match &events[1] {
            ConnEvent::BookInvariantViolated { err } => {
                assert!(
                    matches!(err, ApplyError::Crossed { .. }),
                    "ожидался Crossed, получено {err:?}"
                );
            }
            other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
        }
    }
}
