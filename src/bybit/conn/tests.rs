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
    fn new(sessions: Vec<Vec<Result<Frame, TransportError>>>) -> (Self, Arc<Mutex<Vec<String>>>) {
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

    fn connect(&mut self) -> impl Future<Output = Result<SendFailsOnCall, TransportError>> + Send {
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
/// FIX 2). Session-по-сценарию (как у `ScriptedConnector`) здесь не
/// годится: конечная очередь рано или поздно кончается, и `connect()`
/// начинает возвращать `Err` — путь, который был корректен и ДО FIX 2
/// (растущий бэкофф на неудачном коннекте никогда не был багом), и он
/// замаскировал бы дефект, который тест обязан ловить, — бэкофф выглядел
/// бы растущим по совершенно другой, не относящейся к делу причине.
/// Поэтому сессии здесь не кончаются никогда. Момент вызова `connect()`
/// коннектор больше не запоминает — тест FIX 2 читает запрошенные
/// задержки бэкоффа через подменяемый `Backoff`, а не настоящие часы
/// (`std::time::Instant`), поэтому и считать не нужно.
struct AlwaysCloseImmediatelyConnector;

impl TransportConnector for AlwaysCloseImmediatelyConnector {
    type Transport = ScriptedTransport;

    async fn connect(&mut self) -> Result<ScriptedTransport, TransportError> {
        Ok(ScriptedTransport {
            inbox: VecDeque::from(vec![Ok(Frame::Closed)]),
            sent: Arc::new(Mutex::new(Vec::new())),
        })
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
        // Таймаут приёма (V5) в большинстве тестов вне фокуса так же, как пинг:
        // сценарии со «замолчавшим» сокетом задают свой короткий порог сами.
        recv_timeout: Duration::from_secs(3600),
        backoff: BackoffConfig {
            initial: Duration::from_millis(1),
            max: Duration::from_millis(5),
            multiplier: 2,
        },
    }
}

/// Конфигурация коллектора сессии: то же соединение, но с двумя потоками
/// стакана (`SUBSCRIBED_DEPTHS`, T45). Одно-символьные вызывающие
/// (`lob record`, `pick::measure`) остаются на одном `.50` — этот шов и
/// проверяет, что набор потоков приходит из конфигурации, а не зашит в
/// `Connection`.
fn test_cfg_with_both_depths(symbol: &str) -> PoolConnConfig {
    let mut cfg: PoolConnConfig = test_cfg(symbol).into();
    assert_eq!(
        cfg.depths,
        vec![ORDERBOOK_DEPTH],
        "одно-символьный вызывающий обязан остаться на быстром потоке"
    );
    cfg.depths = SUBSCRIBED_DEPTHS.to_vec();
    cfg
}

fn orderbook_msg(
    kind: &str,
    u: u64,
    cts_ms: i64,
    bids: &[(f64, f64)],
    asks: &[(f64, f64)],
) -> String {
    orderbook_msg_at(ORDERBOOK_DEPTH, kind, u, cts_ms, bids, asks)
}

/// То же сообщение из топика заданной глубины (T45): `.50` — быстрый поток,
/// `.200` — глубокий. Fixture для проверки, что разрыв `u` одного потока не
/// трогает книгу и ресинк другого.
fn orderbook_msg_at(
    depth: u32,
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
        r#"{{"topic":"orderbook.{depth}.SOLUSDT","type":"{kind}","ts":{cts_ms},"data":{{"b":[{}],"a":[{}],"u":{u},"seq":{u}}},"cts":{cts_ms}}}"#,
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

/// Приёмник, который помнит **индекс инструмента** каждого события: у отказа
/// подписки (A8.3) вся суть в привязке к топику, а обычный
/// `mpsc::Sender<ConnEvent>` её теряет по построению (его `ConnSink` индекс
/// игнорирует).
struct IndexSink(mpsc::Sender<(u16, ConnEvent)>);

impl ConnSink for IndexSink {
    async fn send_event(&self, symbol_idx: u16, ev: ConnEvent) {
        let _ = self.0.send((symbol_idx, ev)).await;
    }
}

/// Соединение с двумя инструментами — тот же вид, что у коллектора сессии
/// (один сокет несёт топики многих инструментов).
fn test_pool_cfg_two_symbols() -> PoolConnConfig {
    PoolConnConfig {
        symbols: vec![
            SymbolSpec {
                symbol: "AAAUSDT".to_string(),
                tick_e9: 100_000,
                step_e9: 1_000_000,
                index: 0,
            },
            SymbolSpec {
                symbol: "BBBUSDT".to_string(),
                tick_e9: 100_000,
                step_e9: 1_000_000,
                index: 1,
            },
        ],
        depths: vec![ORDERBOOK_DEPTH],
        ping_interval: Duration::from_secs(3600),
        recv_timeout: Duration::from_secs(3600),
        backoff: BackoffConfig {
            initial: Duration::from_millis(1),
            max: Duration::from_millis(5),
            multiplier: 2,
        },
    }
}

fn snapshot_msg(symbol: &str, u: u64) -> String {
    format!(
        r#"{{"topic":"orderbook.50.{symbol}","type":"snapshot","ts":1,"data":{{"b":[["1.0","5.0"]],"a":[["1.0001","4.0"]],"u":{u},"seq":{u}}}}}"#
    )
}

/// A8.3 (замер 2026-09-18): ответ биржи на подписку с `success:false` обязан
/// стать `ConnEvent::SubscribeFailed` **с индексом инструмента из топика**, а
/// не остаться служебным сообщением. Сокет несёт два инструмента: отказ по
/// топику второго приходит индексом 1, а первый продолжает жить — его снапшот
/// доходит следующим событием.
#[tokio::test]
async fn refused_subscription_is_attributed_to_the_topic_instrument_and_neighbours_live() {
    let refused = r#"{"success":false,"ret_msg":"error:handler not found,topic:orderbook.50.BBBUSDT","conn_id":"c","req_id":"","op":"subscribe"}"#;
    let frames = vec![
        Ok(Frame::Text(refused.to_string())),
        Ok(Frame::Text(snapshot_msg("AAAUSDT", 1))),
    ];
    let (connector, _sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(connector, test_pool_cfg_two_symbols()).run(SystemClock, IndexSink(tx)),
    );

    let first = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("отказ подписки обязан дойти, а не потеряться как служебное")
        .expect("канал жив");
    match first {
        (1, ConnEvent::SubscribeFailed { topic, ret_msg, .. }) => {
            assert_eq!(
                topic.as_deref(),
                Some("orderbook.50.BBBUSDT"),
                "индекс события — инструмент топика из отказа"
            );
            assert!(ret_msg.contains("handler not found"), "{ret_msg}");
        }
        other => panic!("ждали SubscribeFailed индекса 1, получили {other:?}"),
    }
    // Сосед по сокету не задет: его снапшот идёт обычным рыночным событием.
    let second = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("сосед обязан продолжить работу")
        .expect("канал жив");
    match second {
        (0, ConnEvent::Message { event, .. }) => {
            assert!(matches!(event, Event::Book(_)), "{event:?}");
        }
        other => panic!("ждали рыночное событие индекса 0, получили {other:?}"),
    }
    handle.abort();
}

/// A8.3: отказ по топику, которого нет в пуле этого сокета, привязывается к
/// сокету (первому инструменту) — как события без символа; молчания нет.
#[tokio::test]
async fn refused_subscription_with_an_unknown_topic_falls_back_to_the_socket() {
    let refused = r#"{"success":false,"ret_msg":"error:handler not found,topic:orderbook.50.CCCUSDT","op":"subscribe"}"#;
    let (connector, _sent) =
        ScriptedConnector::new(vec![vec![Ok(Frame::Text(refused.to_string()))]]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(connector, test_pool_cfg_two_symbols()).run(SystemClock, IndexSink(tx)),
    );

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("отказ обязан дойти и с чужим топиком")
        .expect("канал жив");
    match ev {
        (0, ConnEvent::SubscribeFailed { topic, .. }) => {
            assert_eq!(topic.as_deref(), Some("orderbook.50.CCCUSDT"));
        }
        other => panic!("ждали SubscribeFailed индекса 0 (сокет), получили {other:?}"),
    }
    handle.abort();
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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 3).await;
    handle.abort();

    for ev in events {
        match ev {
            ConnEvent::Message {
                local_ts_ns, event, ..
            } => {
                let exch_ts_ns = match event {
                    Event::Book(u) => u.cts_ms * 1_000_000,
                    Event::Trade(t) => t.exch_ms * 1_000_000,
                    // Служебные сообщения времени матчинга не несут (A8.3
                    // добавил к ним отказ подписки — он приходит своим
                    // `ConnEvent::SubscribeFailed`, не через `Message`).
                    Event::Other | Event::SubscribeFailed { .. } => continue,
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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

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
            depth: 50,
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

/// T45: потоки глубины независимы. Разрыв `u` в глубоком потоке (`.200`)
/// обязан дать `SequenceGap` **своей** глубины, ресинк-подписку **своего**
/// топика и не тронуть быстрый поток: следующая дельта `.50` идёт дальше
/// как ни в чём не бывало. Обратное — то же самое, потому что состояние
/// (`books`/`resyncing`) хранится на пару (инструмент, поток), а не на
/// инструмент.
#[tokio::test]
async fn gap_in_one_depth_stream_resyncs_only_that_stream() {
    let frames = vec![
        // Быстрый поток: снапшот u=10 и следующая дельта u=11.
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEPTH,
            "snapshot",
            10,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        // Глубокий поток: снапшот u=5, затем дельта u=7 — разрыв (ждали 6).
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEEP_DEPTH,
            "snapshot",
            5,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        ))),
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEEP_DEPTH,
            "delta",
            7,
            2,
            &[(1.0, 6.0)],
            &[],
        ))),
        // Быстрый поток продолжается: его `u` не терялся, ресинка быть не
        // должно, и событие обязано дойти до канала.
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEPTH,
            "delta",
            11,
            3,
            &[(1.0, 7.0)],
            &[],
        ))),
        // Ресинк глубокого потока: свежий снапшот u=100.
        Ok(Frame::Text(orderbook_msg_at(
            ORDERBOOK_DEEP_DEPTH,
            "snapshot",
            100,
            4,
            &[(2.0, 1.0)],
            &[(2.0001, 1.0)],
        ))),
    ];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(connector, test_cfg_with_both_depths("SOLUSDT")).run(SystemClock, tx),
    );

    // Пять событий: .50 снапшот, .200 снапшот, разрыв .200, .50 дельта,
    // .200 ресинк-снапшот. Дельты .200 между разрывом и снапшотом наружу не
    // идут, лишних `SequenceGap` тоже нет.
    let events = collect_n(&mut rx, 5).await;
    handle.abort();

    match &events[0] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => {
            assert_eq!((u.depth, u.u), (ORDERBOOK_DEPTH, 10));
        }
        other => panic!("ожидался снапшот .50 u=10, получено {other:?}"),
    }
    match &events[1] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => {
            assert_eq!((u.depth, u.u), (ORDERBOOK_DEEP_DEPTH, 5));
        }
        other => panic!("ожидался снапшот .200 u=5, получено {other:?}"),
    }
    assert_eq!(
        events[2],
        ConnEvent::SequenceGap {
            depth: ORDERBOOK_DEEP_DEPTH,
            expected: 6,
            got: 7
        },
        "разрыв обязан быть помечен своим потоком"
    );
    match &events[3] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!(
            (u.depth, u.u),
            (ORDERBOOK_DEPTH, 11),
            "разрыв .200 не имеет права глушить .50"
        ),
        other => panic!("ожидалась дельта .50 u=11, получено {other:?}"),
    }
    match &events[4] {
        ConnEvent::Message {
            event: Event::Book(u),
            ..
        } => assert_eq!((u.depth, u.u), (ORDERBOOK_DEEP_DEPTH, 100)),
        other => panic!("ожидался ресинк-снапшот .200 u=100, получено {other:?}"),
    }

    let sent = sent.lock().unwrap();
    let fast_resubscribes = sent
        .iter()
        .filter(|m| m.contains(r#""orderbook.50.SOLUSDT""#))
        .count();
    let deep_resubscribes = sent
        .iter()
        .filter(|m| m.contains(r#""orderbook.200.SOLUSDT""#))
        .count();
    assert_eq!(
        fast_resubscribes, 1,
        "быстрый поток подписан один раз (общий sub_pool) и ни разу не ресинкан"
    );
    assert_eq!(
        deep_resubscribes, 2,
        "глубокий поток: общий sub_pool плюс ровно один ресинк — не по подписке на дельту"
    );
}

/// Одно-символьные вызывающие (`commands::record`, `pick::measure`) ведут
/// одну книгу и один бинлог — их соединение подписано только на `.50`
/// (`From<ConnConfig>`). Приди такому соединению кадр `.200`, он обязан стать
/// `Unrouted`, а не лечь в ту же книгу вторым потоком: у `.200` своя
/// последовательность `u`, и смешение выглядело бы разрывом на каждом втором
/// сообщении (T45).
#[tokio::test]
async fn deep_topic_is_unrouted_on_a_single_depth_connection() {
    let frames = vec![Ok(Frame::Text(orderbook_msg_at(
        ORDERBOOK_DEEP_DEPTH,
        "snapshot",
        5,
        1,
        &[(1.0, 5.0)],
        &[(1.0001, 4.0)],
    )))];
    let (connector, sent) = ScriptedConnector::new(vec![frames]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 1).await;
    handle.abort();
    match &events[0] {
        ConnEvent::Unrouted { local_ts_ns } => {
            assert!(*local_ts_ns >= 0, "метка кадра обязана быть настоящей");
        }
        other => {
            panic!("кадр не нашего потока наружу как рынок не идёт, ожидался Unrouted: {other:?}")
        }
    }
    let sent = sent.lock().unwrap();
    assert!(
        sent.iter().all(|m| !m.contains("orderbook.200.SOLUSDT")),
        "в подписке одно-символьного соединения глубокого топика быть не должно: {sent:?}"
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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

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
    assert_eq!(
        events[1],
        ConnEvent::Disconnected {
            first_of_socket: true
        }
    );
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
/// сессию, 2 — повторная `sub_orderbook` на ресинке после
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
            // Отправка №1 — подписка на весь набор сокета одним
            // сообщением (`ws::sub_pool`, таск 28; до него подписок было
            // две — стакан и лента — и ресинк был третьей отправкой),
            // №2 — ресинк-подписка, которая и обязана упасть.
            fail_on_call: 2,
        }),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

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
            depth: 50,
            expected: 11,
            got: 12
        }
    );
    assert_eq!(
        events[2],
        ConnEvent::Disconnected {
            first_of_socket: true
        },
        "неудавшаяся отправка ресинка — тоже смерть сокета и обязана дать Disconnected, \
         как и два других пути в select!"
    );
}

/// Бэкофф, который не ждёт по-настоящему: записывает запрошенную
/// задержку и сразу возвращает готовое будущее — ровно то, что нужно
/// FIX 2, доказанному ниже без единой миллисекунды настоящего времени.
/// После `halt_after` записей возвращает будущее, которое не завершается
/// никогда (`std::future::pending`) — цикл `Connection::run_with_backoff`
/// сам останавливается на достаточном числе раундов вместо того, чтобы
/// тест гадал, сколько реальных миллисекунд ждать, пока раунды накопятся.
struct RecordingBackoff {
    waited: Arc<Mutex<Vec<Duration>>>,
    halt_after: usize,
}

impl Backoff for RecordingBackoff {
    fn wait(&self, dur: Duration) -> impl Future<Output = ()> + Send {
        let stop = {
            let mut guard = self.waited.lock().unwrap();
            guard.push(dur);
            guard.len() >= self.halt_after
        };
        async move {
            if stop {
                std::future::pending::<()>().await;
            }
        }
    }
}

/// FIX 2. Сервер, который принимает рукопожатие и тут же рвёт соединение
/// (рейт-лимит, сброс нагрузки, дефектный прокси), не должен держать цикл
/// на полу бэкоффа вечно. Ни одна из сессий `AlwaysCloseImmediatelyConnector`
/// не пересылает ни одного сообщения (только мгновенный `Frame::Closed`),
/// поэтому счётчик попыток обязан расти от раунда к раунду, а не
/// сбрасываться по одному лишь факту успешной подписки.
///
/// Раньше тест мерил настоящие интервалы `std::time::Instant` между
/// вызовами `connect()` — реальные часы под нагрузкой CI время от времени
/// дают дребезг ровно там, где порог не рассчитан на него (наблюдалось:
/// первая задержка 64мс при пороге «< 60мс у пола ~3мс»). `RecordingBackoff`
/// убирает настоящее время из проверки целиком: `run_with_backoff` зовёт
/// `backoff.wait(delay_for_attempt(attempt))`, и это ровно то значение,
/// что здесь читается — без побочного шума планировщика ОС. `tokio::time::
/// timeout` ниже — не источник данных для проверки, а только защита от
/// зависания на сломанном коде (тот же приём, что уже даёт `collect_n`
/// в FIX 1).
#[tokio::test]
async fn connect_then_close_without_forwarding_a_message_does_not_reset_backoff() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.backoff = BackoffConfig {
        initial: Duration::from_millis(3),
        max: Duration::from_millis(150),
        multiplier: 5,
    };
    const SAMPLES: usize = 8;
    let waited: Arc<Mutex<Vec<Duration>>> = Arc::new(Mutex::new(Vec::new()));
    let backoff = RecordingBackoff {
        waited: waited.clone(),
        halt_after: SAMPLES,
    };
    let (tx, _rx) = mpsc::channel(16);
    let handle = tokio::spawn(
        Connection::new(AlwaysCloseImmediatelyConnector, cfg).run_with_backoff(
            SystemClock,
            tx,
            backoff,
        ),
    );

    // Планировщик здесь не спит по-настоящему (`RecordingBackoff`), так
    // что `SAMPLES` задержек собираются за считаные переключения задачи —
    // `yield_now` отдаёт управление рантайму ровно затем, чтобы дать
    // спавнутой задаче продвинуться, не полагаясь на реальное время;
    // `timeout` — только потолок на случай, если код сломан и цикл
    // никогда не доходит до `SAMPLES` записей.
    let collected = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if waited.lock().unwrap().len() >= SAMPLES {
                return;
            }
            tokio::task::yield_now().await;
        }
    })
    .await;
    handle.abort();
    collected.expect("сбор образцов бэкоффа завис — цикл не дошёл до SAMPLES раундов");

    let gaps = waited.lock().unwrap().clone();
    assert!(
        gaps.len() >= 5,
        "ожидались хотя бы 5 задержек, получено {}",
        gaps.len()
    );
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
/// `now_ns()` вызывается заново на каждый кадр, а не переиспользуется.
///
/// Таск 04 добавил вторую метку, `parsed_ts_ns`, сразу после
/// `ws::parse_message` (суббюджет «разбор», `PLAN.md` 3.1) — на каждый
/// успешно распарсенный кадр (все четыре здесь — валидные `orderbook`)
/// часы теперь читаются ровно дважды, не один раз; сверка счётчика с
/// `2 * n_frames` доказывает, что ни одного лишнего чтения сверх этих
/// двух не просочилось.
#[tokio::test]
async fn local_ts_advances_exactly_twice_per_completed_recv_and_is_strictly_increasing() {
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
        n_frames * 2,
        "часы обязаны читаться ровно дважды на каждый успешно распарсенный recv \
         (`local_ts_ns` до разбора, `parsed_ts_ns` сразу после) — не реже (одно \
         чтение на сессию не прошло бы строгий рост) и не чаще"
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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 2).await; // Message(u=10) → BookInvariantViolated
    handle.abort();

    match &events[1] {
        ConnEvent::BookInvariantViolated { err, .. } => {
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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 2).await;
    handle.abort();

    match &events[1] {
        ConnEvent::BookInvariantViolated { err, .. } => {
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
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 2).await;
    handle.abort();

    match &events[1] {
        ConnEvent::BookInvariantViolated { err, .. } => {
            assert!(
                matches!(err, ApplyError::Crossed { .. }),
                "ожидался Crossed, получено {err:?}"
            );
        }
        other => panic!("ожидался BookInvariantViolated, получено {other:?}"),
    }
}

/// K1 (2026-09-17): отказ рукопожатия больше не молчит. Коннектор, который
/// всегда отвечает `HTTP 403` (гео-блок), обязан дать событие `ConnectFailed`
/// с **самим статусом** и номером попытки: до правки ошибка проглатывалась в
/// `Err(_)`, и устойчивый 403/429 выглядел как вечно тихий ретрай — ни строки
/// `gaps.csv`, ни счётчика.
#[tokio::test]
async fn failed_handshake_is_reported_with_its_http_status() {
    struct Http403Connector;
    impl TransportConnector for Http403Connector {
        type Transport = ScriptedTransport;
        async fn connect(&mut self) -> Result<ScriptedTransport, TransportError> {
            Err(TransportError::Http {
                status: 403,
                msg: "Forbidden".to_string(),
            })
        }
    }

    let (tx, mut rx) = mpsc::channel(16);
    let handle =
        tokio::spawn(Connection::new(Http403Connector, test_cfg("SOLUSDT")).run(SystemClock, tx));
    let events = collect_n(&mut rx, 1).await;
    handle.abort();

    match &events[0] {
        ConnEvent::ConnectFailed {
            attempt,
            http_status,
            err,
            ..
        } => {
            assert_eq!(*http_status, Some(403), "{err}");
            assert_eq!(*attempt, 1, "нумерация попыток с единицы");
            assert!(err.contains("Forbidden"), "{err}");
        }
        other => panic!("ожидался ConnectFailed, получено {other:?}"),
    }
    // Обрыв (таймаут/TLS) статуса не имеет — и это не `Some(0)`.
    assert_eq!(
        TransportError::Io("обрыв".to_string()).http_status(),
        None,
        "у обрыва нет HTTP-статуса"
    );
}

/// A8.5 (2026-09-18): сеть пропала на часы — серия отказов подряд. Каждый отказ
/// виден (`ConnectFailed` со своим номером попытки), а причины **отличимы**:
/// HTTP-отказ несёт статус (403/429), DNS-отказ статуса не имеет — только текст
/// (`TransportError::Io`). Восстановление — не «продолжаем как ни в чём не
/// бывало»: новый сокет подписывается заново, и данные возвращаются обычным
/// `Message` (снапшот).
#[tokio::test]
async fn repeated_connect_failures_are_each_reported_and_a_recovered_socket_resubscribes() {
    /// Коннектор с очередью отказов: первые `connect()` падают, следующий
    /// отдаёт сценарий кадров — «сеть вернулась».
    struct FlakyConnectConnector {
        failures: VecDeque<TransportError>,
        frames: Option<VecDeque<Result<Frame, TransportError>>>,
        sent: Arc<Mutex<Vec<String>>>,
    }
    impl TransportConnector for FlakyConnectConnector {
        type Transport = ScriptedTransport;
        fn connect(
            &mut self,
        ) -> impl Future<Output = Result<ScriptedTransport, TransportError>> + Send {
            let failure = self.failures.pop_front();
            // Кадры забираются только удачной попытке: иначе первый же отказ
            // съел бы сценарий, и «восстановление» осталось бы без данных.
            let frames = if failure.is_some() {
                None
            } else {
                self.frames.take()
            };
            let sent = self.sent.clone();
            async move {
                if let Some(err) = failure {
                    return Err(err);
                }
                Ok(ScriptedTransport {
                    inbox: frames.unwrap_or_default(),
                    sent,
                })
            }
        }
    }

    let sent = Arc::new(Mutex::new(Vec::new()));
    let connector = FlakyConnectConnector {
        failures: VecDeque::from(vec![
            TransportError::Http {
                status: 429,
                msg: "Too Many Requests".to_string(),
            },
            // DNS-отказ: адрес не разрешился, HTTP-статуса нет.
            TransportError::Io("dns: failed to lookup address information".to_string()),
        ]),
        frames: Some(VecDeque::from(vec![Ok(Frame::Text(orderbook_msg(
            "snapshot",
            1,
            1,
            &[(1.0, 5.0)],
            &[(1.0001, 4.0)],
        )))])),
        sent: sent.clone(),
    };
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, test_cfg("SOLUSDT")).run(SystemClock, tx));

    let events = collect_n(&mut rx, 3).await;
    handle.abort();

    match &events[0] {
        ConnEvent::ConnectFailed {
            attempt,
            http_status,
            err,
            ..
        } => {
            assert_eq!(*attempt, 1, "первая попытка");
            assert_eq!(*http_status, Some(429), "429 отличим от 403 и от DNS");
            assert!(err.contains("Too Many"), "{err}");
        }
        other => panic!("ждали ConnectFailed, получили {other:?}"),
    }
    match &events[1] {
        ConnEvent::ConnectFailed {
            attempt,
            http_status,
            err,
            ..
        } => {
            assert_eq!(
                *attempt, 2,
                "номер попытки растёт, пока сессия не была полезной"
            );
            assert_eq!(
                *http_status, None,
                "DNS-отказ статуса не имеет — отличается от 403/429"
            );
            assert!(err.contains("lookup"), "{err}");
        }
        other => panic!("ждали ConnectFailed, получили {other:?}"),
    }
    match &events[2] {
        ConnEvent::Message { event, .. } => {
            assert!(
                matches!(event, Event::Book(u) if u.is_snapshot),
                "после восстановления приходит снапшот: {event:?}"
            );
        }
        other => panic!("ждали снапшот после восстановления, получили {other:?}"),
    }
    // Восстановленный сокет подписался заново — «вернулись» не молча.
    let sent = sent.lock().unwrap().clone();
    assert!(
        sent.iter().any(|m| m.contains("orderbook.50.SOLUSDT")),
        "подписка после восстановления обязана уйти: {sent:?}"
    );
}

/// V5 (2026-09-17): полуживое соединение — TCP жив, данных нет — раньше висело
/// бесконечно. Порог приёма — 2 × интервал пинга; здесь транспорт молчит
/// (`pending()` на пустом ящике), поэтому событие обязано прийти, а не висеть.
#[tokio::test]
async fn silent_socket_times_out_and_reports_disconnected() {
    let mut cfg = test_cfg("SOLUSDT");
    cfg.ping_interval = Duration::from_secs(3600); // в фокусе — приём, не пинг
    cfg.recv_timeout = Duration::from_millis(40);
    let (connector, _sent) = ScriptedConnector::new(vec![Vec::new()]);
    let (tx, mut rx) = mpsc::channel(16);
    let handle = tokio::spawn(Connection::new(connector, cfg).run(SystemClock, tx));

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("молчание длиннее 2 × пинга обязано дать событие, а не висеть")
        .expect("канал жив");
    handle.abort();

    assert!(
        matches!(
            ev,
            ConnEvent::Disconnected {
                first_of_socket: true
            }
        ),
        "молчащий сокет уходит в переподключение тем же путём, что обрыв: {ev:?}"
    );
}
