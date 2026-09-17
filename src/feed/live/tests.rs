use super::*;
use crate::bybit::conn::{Frame, Transport, TransportError, ORDERBOOK_DEEP_DEPTH};
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

/// Тот же сценарный транспорт, но с журналом отправленного — нужен
/// проверке «переподписался только один символ сокета».
struct RecordingTransport {
    inbox: Arc<Mutex<VecDeque<Result<Frame, TransportError>>>>,
    sent: Arc<Mutex<Vec<String>>>,
}

impl Transport for RecordingTransport {
    async fn send_text(&mut self, msg: String) -> Result<(), TransportError> {
        self.sent.lock().unwrap().push(msg);
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

struct RecordingConnector {
    inbox: Arc<Mutex<VecDeque<Result<Frame, TransportError>>>>,
    sent: Arc<Mutex<Vec<String>>>,
}

impl TransportConnector for RecordingConnector {
    type Transport = RecordingTransport;
    fn connect(
        &mut self,
    ) -> impl Future<Output = Result<RecordingTransport, TransportError>> + Send {
        let inbox = self.inbox.clone();
        let sent = self.sent.clone();
        async move { Ok(RecordingTransport { inbox, sent }) }
    }
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
    let mut feed = LiveFeed::spawn_with(pool, move |_m| OneShotConnector {
        inbox: inbox.clone(),
    })
    .unwrap();

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
        move |_m| OneShotConnector {
            inbox: inbox.clone(),
        },
        clock,
    )
    .unwrap();

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

/// Таск 28, критерий «раскладка подписок по соединениям»: один сокет
/// несёт топики двух инструментов, и каждый доходит **своим** индексом
/// пула. Проверяется и то, и другое разом: коннектор создан ровно один
/// (значит соединение действительно одно на два символа), а `Feed`
/// отдал события с индексами 0 и 1.
///
/// Третий кадр — дельта первого инструмента, продолжающая его `u` через
/// снапшот **второго**. Если бы книга на соединении была одна на всех
/// (как до таска 28), снапшот соседа сбросил бы последовательность и
/// дельта пришла бы разрывом, а не событием.
#[test]
fn one_socket_carries_two_symbols_and_routes_each_to_its_own_index_and_book() {
    let snap_a = r#"{"topic":"orderbook.50.AAAUSDT","type":"snapshot","ts":1,"data":{"s":"AAAUSDT","b":[["100.0","1.0"]],"a":[],"u":1,"seq":1}}"#;
    let snap_b = r#"{"topic":"orderbook.50.BBBUSDT","type":"snapshot","ts":1,"data":{"s":"BBBUSDT","b":[["200.0","1.0"]],"a":[],"u":1,"seq":2}}"#;
    let delta_a = r#"{"topic":"orderbook.50.AAAUSDT","type":"delta","ts":2,"data":{"s":"AAAUSDT","b":[["100.0","2.0"]],"a":[],"u":2,"seq":3}}"#;
    let inbox = Arc::new(Mutex::new(VecDeque::from(vec![
        Ok(Frame::Text(snap_a.to_string())),
        Ok(Frame::Text(snap_b.to_string())),
        Ok(Frame::Text(delta_a.to_string())),
    ])));
    let pool = vec![
        PoolMember {
            symbol: "AAAUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
        PoolMember {
            symbol: "BBBUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
    ];
    let connectors = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let made = connectors.clone();
    let mut feed = LiveFeed::spawn_with(pool, move |_m| {
        made.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        OneShotConnector {
            inbox: inbox.clone(),
        }
    })
    .unwrap();

    let mut seen: Vec<u16> = Vec::new();
    for _ in 0..3 {
        match feed.next_event().expect("все три кадра обязаны дойти") {
            Event::Market { symbol, .. } => seen.push(symbol),
            other => panic!("ждали Market, получили {other:?}"),
        }
    }
    assert_eq!(
        seen,
        vec![0, 1, 0],
        "каждое сообщение обязано прийти с индексом СВОЕГО инструмента;              третье — дельта первого после снапшота второго, значит книги раздельные"
    );
    assert_eq!(
        connectors.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "два инструмента обязаны уместиться в одно соединение (предел args              Bybit 21000 символов и близко не достигнут)"
    );
}

/// Таск 28: раскладка ломается ровно там, где кончается
/// задокументированный предел `args` одного публичного соединения
/// (21 000 символов), а не на назначенном числе инструментов.
/// Фикстура набрана так, чтобы сумма была **ровно** пределом. T45 добавил
/// третий топик на инструмент, поэтому арифметика такая: `orderbook.50.` +
/// `orderbook.200.` + `publicTrade.` = 39 символов плюс три длины имени.
/// 258 имён по 14 символов (39 + 42 = 81 на инструмент → 20 898) плюс одно
/// имя в 21 символ (39 + 63 = 102) — 21 000 в точности. Ровный предел обязан
/// влезть в одно соединение: иначе `>` и `>=` в раскладке неразличимы.
#[test]
fn connections_split_exactly_at_the_documented_args_limit() {
    let short = |i: usize| PoolMember {
        symbol: format!("{i:0>10}USDT"),
        tick_e9: 1,
        step_e9: 1,
    };
    let long = PoolMember {
        symbol: format!("{:0>17}USDT", 0),
        tick_e9: 1,
        step_e9: 1,
    };
    assert_eq!(short(0).symbol.len(), 14);
    assert_eq!(long.symbol.len(), 21);
    let per_short = 81;
    let per_long = 102;
    let shorts = 258;
    assert_eq!(shorts * per_short + per_long, MAX_ARGS_CHARS);
    assert_eq!(
        per_short,
        crate::bybit::ws::pool_args_chars(&SUBSCRIBED_DEPTHS, &short(0).symbol)
    );
    assert_eq!(
        per_long,
        crate::bybit::ws::pool_args_chars(&SUBSCRIBED_DEPTHS, &long.symbol)
    );

    let mut exactly: Vec<PoolMember> = (0..shorts).map(short).collect();
    exactly.push(long.clone());
    assert_eq!(
        plan_connections(&exactly, &SUBSCRIBED_DEPTHS)
            .unwrap()
            .len(),
        1,
        "сумма ровно 21 000 обязана влезть в одно соединение"
    );

    let mut one_more = exactly.clone();
    one_more.push(short(shorts));
    let groups = plan_connections(&one_more, &SUBSCRIBED_DEPTHS).unwrap();
    assert_eq!(
        groups.len(),
        2,
        "первый символ сверх предела уезжает во второе соединение"
    );
    assert_eq!(groups[0].len(), shorts + 1);
    // Индексы: 0..394 — короткие, 395 — длинное имя, 396 — добавленный сверх предела.
    assert_eq!(groups[1], vec![u16::try_from(shorts + 1).unwrap()]);
    let flat: Vec<u16> = groups.into_iter().flatten().collect();
    assert_eq!(
        flat,
        (0..=u16::try_from(shorts + 1).unwrap()).collect::<Vec<_>>(),
        "раскладка не вправе переставлять пул: индекс i остаётся индексом i"
    );
}

/// Пустой пул — ошибка раскладки, а не ноль соединений: `Feed` без
/// единого отправителя навсегда повесил бы `next_event` на
/// `blocking_recv`.
#[test]
fn empty_pool_is_a_layout_error_not_a_feed_that_never_speaks() {
    assert_eq!(
        plan_connections(&[], &SUBSCRIBED_DEPTHS),
        Err(LayoutError::EmptyPool)
    );
    let spawned = LiveFeed::spawn_with(Vec::new(), |_m: &PoolMember| OneShotConnector {
        inbox: Arc::new(Mutex::new(VecDeque::new())),
    });
    assert!(spawned.is_err(), "поднять Feed на пустом пуле нельзя");
}

/// Блокирующее ревью таска 28: разрыв сокета — событие **каждого**
/// инструмента этого сокета. Иначе на 761 инструменте 760 из них не
/// сбросят `synced` и не получат ни строки `gaps.csv`. Ровно один из
/// них помечен «первым» — по нему считается одно переподключение на
/// сокет.
#[test]
fn socket_close_reaches_every_symbol_of_that_socket_and_counts_once() {
    let inbox = Arc::new(Mutex::new(VecDeque::from(vec![Ok(Frame::Closed)])));
    let pool = vec![
        PoolMember {
            symbol: "AAAUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
        PoolMember {
            symbol: "BBBUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
    ];
    let mut feed = LiveFeed::spawn_with(pool, move |_m| OneShotConnector {
        inbox: inbox.clone(),
    })
    .unwrap();

    let mut seen: Vec<(u16, GapKind)> = Vec::new();
    for _ in 0..2 {
        match feed
            .next_event()
            .expect("оба инструмента обязаны узнать о разрыве")
        {
            Event::Gap { symbol, kind, .. } => seen.push((symbol, kind)),
            other => panic!("ждали Gap, получили {other:?}"),
        }
    }
    assert_eq!(
        seen,
        vec![
            (0, GapKind::Disconnected),
            (1, GapKind::DisconnectedSameSocket)
        ],
        "разрыв доходит до каждого инструмента сокета, но считается один раз"
    );
}

/// Ресинк одного символа на общем сокете не глушит соседа: A теряет
/// `u`, переподписывается **только** A, а B на том же сокете продолжает
/// отдавать события и переподписки не получает.
#[test]
fn resync_of_one_symbol_does_not_touch_the_other_on_the_same_socket() {
    let snap_a = r#"{"topic":"orderbook.50.AAAUSDT","type":"snapshot","ts":1,"data":{"b":[["100.0","1.0"]],"a":[],"u":1,"seq":1}}"#;
    let snap_b = r#"{"topic":"orderbook.50.BBBUSDT","type":"snapshot","ts":1,"data":{"b":[["200.0","1.0"]],"a":[],"u":1,"seq":2}}"#;
    // Разрыв `u` у A: ждали 2, пришло 7 — ресинк только A.
    let gap_a = r#"{"topic":"orderbook.50.AAAUSDT","type":"delta","ts":2,"data":{"b":[["100.0","3.0"]],"a":[],"u":7,"seq":3}}"#;
    let delta_b = r#"{"topic":"orderbook.50.BBBUSDT","type":"delta","ts":3,"data":{"b":[["200.0","2.0"]],"a":[],"u":2,"seq":4}}"#;
    let sent = Arc::new(Mutex::new(Vec::<String>::new()));
    let inbox = Arc::new(Mutex::new(VecDeque::from(vec![
        Ok(Frame::Text(snap_a.to_string())),
        Ok(Frame::Text(snap_b.to_string())),
        Ok(Frame::Text(gap_a.to_string())),
        Ok(Frame::Text(delta_b.to_string())),
    ])));
    let pool = vec![
        PoolMember {
            symbol: "AAAUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
        PoolMember {
            symbol: "BBBUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
    ];
    let (inbox_c, sent_c) = (inbox.clone(), sent.clone());
    let mut feed = LiveFeed::spawn_with(pool, move |_m| RecordingConnector {
        inbox: inbox_c.clone(),
        sent: sent_c.clone(),
    })
    .unwrap();

    assert!(matches!(
        feed.next_event().unwrap(),
        Event::Market { symbol: 0, .. }
    ));
    assert!(matches!(
        feed.next_event().unwrap(),
        Event::Market { symbol: 1, .. }
    ));
    match feed.next_event().unwrap() {
        Event::Gap {
            symbol: 0,
            kind: GapKind::SequenceGap,
            ..
        } => {}
        other => panic!("A обязан дать разрыв последовательности: {other:?}"),
    }
    assert!(
        matches!(feed.next_event().unwrap(), Event::Market { symbol: 1, .. }),
        "B на том же сокете обязан продолжить отдавать события, пока A ресинкается"
    );

    let sent = sent.lock().unwrap().clone();
    let resub_a = sent
        .iter()
        .filter(|m| m.contains("orderbook.50.AAAUSDT"))
        .count();
    let resub_b = sent
        .iter()
        .filter(|m| m.contains("orderbook.50.BBBUSDT"))
        .count();
    assert_eq!(
        (resub_a, resub_b),
        (2, 1),
        "одна общая подписка на оба + повторная подписка только на A: {sent:?}"
    );
}

/// T45: набор потоков стакана задаёт **вход** `LiveFeed`, а не глобальная
/// константа. Коллектор сессии (`spawn_with_ticks` и его ядро) подписывается
/// на оба потока — `.50` и `.200`; остальные входы (`spawn_with`,
/// `spawn_with_clock` — им пользуется `lob react`) остаются на одном `.50`,
/// потому что их потребитель ведёт одну книгу. Подпиши их на `.200` — дельты
/// двух разных последовательностей `u` легли бы в одну книгу.
#[test]
fn only_the_tick_entry_subscribes_to_both_depth_streams() {
    let snap = |depth: u32| {
        format!(
            r#"{{"topic":"orderbook.{depth}.BTCUSDT","type":"snapshot","ts":1,"data":{{"b":[["100.0","1.0"]],"a":[["101.0","1.0"]],"u":1,"seq":1}}}}"#
        )
    };
    let pool = || {
        vec![PoolMember {
            symbol: "BTCUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }]
    };
    // Кадр в инбоксе делает наблюдение детерминированным: транспорт обязан
    // отправить подписку **до** первого `recv`, поэтому одно полученное
    // событие доказывает, что подписка уже была.
    let inbox_with = |raw: String| Arc::new(Mutex::new(VecDeque::from(vec![Ok(Frame::Text(raw))])));

    // Однопотоковый вход (`spawn_with`) — как `lob react`.
    let sent = Arc::new(Mutex::new(Vec::<String>::new()));
    let inbox = inbox_with(snap(ORDERBOOK_DEPTH));
    let (inbox_c, sent_c) = (inbox.clone(), sent.clone());
    let mut feed = LiveFeed::spawn_with(pool(), move |_m| RecordingConnector {
        inbox: inbox_c.clone(),
        sent: sent_c.clone(),
    })
    .unwrap();
    match feed.next_event().unwrap() {
        Event::Market { symbol: 0, .. } => {}
        other => panic!("ожидалось рыночное событие быстрого потока, получено {other:?}"),
    }
    let subscribed = sent.lock().unwrap().clone();
    assert!(
        subscribed
            .iter()
            .any(|m| m.contains("orderbook.50.BTCUSDT")),
        "быстрый поток обязан быть в подписке: {subscribed:?}"
    );
    assert!(
        subscribed
            .iter()
            .all(|m| !m.contains("orderbook.200.BTCUSDT")),
        "по умолчанию глубокого потока в подписке быть не должно: {subscribed:?}"
    );

    // Коллектор сессии (`spawn_with_ticks` шлёт `SUBSCRIBED_DEPTHS`): оба
    // потока, и кадр `.200` доходит до потребителя как рыночное событие того
    // же инструмента.
    let sent = Arc::new(Mutex::new(Vec::<String>::new()));
    let inbox = inbox_with(snap(ORDERBOOK_DEEP_DEPTH));
    let (inbox_c, sent_c) = (inbox.clone(), sent.clone());
    let mut feed = LiveFeed::spawn_with_clock_and_connector_and_ticks(
        pool(),
        move |_m| RecordingConnector {
            inbox: inbox_c.clone(),
            sent: sent_c.clone(),
        },
        SystemClock,
        Some(Duration::from_secs(3600)),
        SUBSCRIBED_DEPTHS.to_vec(),
    )
    .unwrap();
    match feed.next_event().unwrap() {
        Event::Market {
            symbol: 0,
            payload: crate::bybit::ws::Event::Book(update),
            ..
        } => assert_eq!(
            update.depth, ORDERBOOK_DEEP_DEPTH,
            "событие глубокого потока обязано дойти до потребителя"
        ),
        other => panic!("ожидалось рыночное событие книги, получено {other:?}"),
    }
    let subscribed = sent.lock().unwrap().clone();
    assert!(
        subscribed
            .iter()
            .any(|m| m.contains("orderbook.50.BTCUSDT"))
            && subscribed
                .iter()
                .any(|m| m.contains("orderbook.200.BTCUSDT")),
        "коллектор сессии обязан подписаться на оба потока глубины: {subscribed:?}"
    );
    feed.stop_handle().stop();
}

/// Рыночный кадр с чужим топиком не исчезает молча — он считается
/// (`GapKind::Unrouted` → `session.json.unrouted`) и **не** приписывается
/// первому инструменту сокета.
#[test]
fn a_market_frame_with_an_unknown_symbol_is_counted_not_silently_dropped() {
    let alien = r#"{"topic":"orderbook.50.ZZZUSDT","type":"snapshot","ts":1,"data":{"b":[["1.0","1.0"]],"a":[],"u":1,"seq":1}}"#;
    let ours = r#"{"topic":"orderbook.50.AAAUSDT","type":"snapshot","ts":1,"data":{"b":[["100.0","1.0"]],"a":[],"u":1,"seq":2}}"#;
    let inbox = Arc::new(Mutex::new(VecDeque::from(vec![
        Ok(Frame::Text(alien.to_string())),
        Ok(Frame::Text(ours.to_string())),
    ])));
    let pool = vec![PoolMember {
        symbol: "AAAUSDT".to_string(),
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
    }];
    let mut feed = LiveFeed::spawn_with(pool, move |_m| OneShotConnector {
        inbox: inbox.clone(),
    })
    .unwrap();

    match feed
        .next_event()
        .expect("чужой кадр обязан дойти счётчиком")
    {
        Event::Gap {
            kind: GapKind::Unrouted,
            ..
        } => {}
        other => panic!("ждали Unrouted, получили {other:?}"),
    }
    assert!(
        matches!(feed.next_event().unwrap(), Event::Market { symbol: 0, .. }),
        "свой кадр обязан дойти следующим, как ни в чём не бывало"
    );
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
        move |_m| OneShotConnector {
            inbox: inbox.clone(),
        },
        clock,
        Some(Duration::from_millis(20)),
        // Тест про тик, не про потоки глубины: одного быстрого достаточно.
        vec![ORDERBOOK_DEPTH],
    )
    .unwrap();
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

/// Таск 34: `LiveFeed::add` на ходу. Стартовый пул — один инструмент на
/// одном соединении; добавленный получает **своё** соединение (коннектор
/// создан второй раз — через ту же сохранённую фабрику) и индекс 1 —
/// продолжение нумерации; события стартового по-прежнему идут индексом 0.
/// Транспорты у соединений разные (свой ящик на каждое), поэтому кадр с
/// индексом 1 мог прийти только с нового сокета: чужой топик соединение
/// отдало бы `Unrouted`, а не `Market`. `stop()` после добавления
/// заканчивает поток `None` — и окончательно, сколько бы новый сокет ни
/// слал дальше.
#[test]
fn add_opens_a_new_connection_with_the_next_index_and_stop_still_ends_the_feed() {
    let snap_a = r#"{"topic":"orderbook.50.AAAUSDT","type":"snapshot","ts":1,"data":{"s":"AAAUSDT","b":[["100.0","1.0"]],"a":[],"u":1,"seq":1}}"#;
    let snap_b = r#"{"topic":"orderbook.50.BBBUSDT","type":"snapshot","ts":1,"data":{"s":"BBBUSDT","b":[["200.0","1.0"]],"a":[],"u":1,"seq":2}}"#;
    let delta_b = r#"{"topic":"orderbook.50.BBBUSDT","type":"delta","ts":2,"data":{"s":"BBBUSDT","b":[["200.0","2.0"]],"a":[],"u":2,"seq":3}}"#;
    let inbox_a = Arc::new(Mutex::new(VecDeque::from(vec![Ok(Frame::Text(
        snap_a.to_string(),
    ))])));
    let inbox_b = Arc::new(Mutex::new(VecDeque::from(vec![
        Ok(Frame::Text(snap_b.to_string())),
        Ok(Frame::Text(delta_b.to_string())),
    ])));
    // Ящики по порядку создания соединений: первый — стартовому, второй —
    // добавленному. Фабрика зовётся один раз на соединение.
    let inboxes = Arc::new(Mutex::new(VecDeque::from(vec![inbox_a, inbox_b])));
    let connectors = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let made = connectors.clone();
    let mut feed = LiveFeed::spawn_with(
        vec![PoolMember {
            symbol: "AAAUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }],
        move |_m| {
            made.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            OneShotConnector {
                inbox: inboxes
                    .lock()
                    .unwrap()
                    .pop_front()
                    .expect("ящиков ровно столько, сколько соединений"),
            }
        },
    )
    .unwrap();

    let first = feed.next_event().expect("снапшот стартового обязан дойти");
    assert!(
        matches!(first, Event::Market { symbol: 0, .. }),
        "стартовый инструмент — индекс 0: {first:?}"
    );
    assert_eq!(connectors.load(std::sync::atomic::Ordering::SeqCst), 1);

    let indices = crate::feed::DynamicPool::add(
        &mut feed,
        vec![PoolMember {
            symbol: "BBBUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }],
    )
    .unwrap();
    assert_eq!(
        indices,
        vec![1],
        "индекс добавленного продолжает нумерацию пула"
    );

    let mut seen: Vec<u16> = Vec::new();
    for _ in 0..2 {
        match feed
            .next_event()
            .expect("оба кадра нового сокета обязаны дойти")
        {
            Event::Market { symbol, .. } => seen.push(symbol),
            other => panic!("ждали Market нового инструмента, получили {other:?}"),
        }
    }
    assert_eq!(
        seen,
        vec![1, 1],
        "снапшот и дельта добавленного приходят его индексом; книга — своя, дельта не разрыв"
    );
    assert_eq!(
        connectors.load(std::sync::atomic::Ordering::SeqCst),
        2,
        "добавленный инструмент — новое соединение той же фабрикой, живое не тронуто"
    );
    assert_eq!(
        feed.shards.len(),
        2,
        "поток ввода-вывода на каждое соединение — и на добавленное тоже"
    );
    assert_eq!(
        feed.shards
            .iter()
            .map(|s| s.handle.is_some())
            .filter(|alive| *alive)
            .count(),
        2,
        "оба потока живы и ещё не отчитались о смерти (V12)"
    );

    feed.stop_handle().stop();
    let mut seen_none = false;
    for _ in 0..1000 {
        if feed.next_event().is_none() {
            seen_none = true;
            break;
        }
    }
    assert!(seen_none, "после stop() поток обязан закончиться None");
    assert!(
        feed.next_event().is_none(),
        "остановка окончательна и для добавленного соединения"
    );
}

/// Индекс — `u16`: партия, с которой пул перестаёт помещаться, отклоняется
/// целиком (`PoolTooLarge`) до открытия хоть одного соединения, а не
/// схлопывается в `u16::MAX`.
#[test]
fn add_beyond_u16_is_a_layout_error_before_any_connection_is_opened() {
    let member = |i: usize| PoolMember {
        symbol: format!("S{i}"),
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
    };
    let fits = shard_specs(&[member(0)], usize::from(u16::MAX), &FAST_DEPTHS);
    assert_eq!(
        fits.unwrap()[0][0].index,
        u16::MAX,
        "последний индекс u16 ещё занимается"
    );
    let overflow = shard_specs(&[member(0), member(1)], usize::from(u16::MAX), &FAST_DEPTHS);
    assert!(
        matches!(
            overflow,
            Err(LayoutError::PoolTooLarge { got }) if got == usize::from(u16::MAX) + 2
        ),
        "партия за пределом u16 — ошибка раскладки: {:?}",
        overflow.map(|g| g.len())
    );
}

/// V12 (2026-09-17): смерть ОС-потока шарда раньше была невидима — его
/// инструменты просто замолкали, и заметить это можно было только по
/// переставшему расти графику. Теперь она видна **на тике**: строка `gaps.csv`
/// на каждый инструмент шарда (первый — `Disconnected`, остальные —
/// `DisconnectedSameSocket`, то есть `reconnects` растёт на один за шард), и
/// **один раз**, а не на каждом тике.
#[test]
fn dead_io_shard_is_reported_once_on_the_tick() {
    let inbox = Arc::new(Mutex::new(VecDeque::new()));
    let pool = vec![
        PoolMember {
            symbol: "BTCUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
        PoolMember {
            symbol: "ETHUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        },
    ];
    let clock = FakeSeqClock(Arc::new(std::sync::atomic::AtomicI64::new(0)));
    let mut feed = LiveFeed::spawn_with_clock_and_connector_and_ticks(
        pool,
        move |_m| OneShotConnector {
            inbox: inbox.clone(),
        },
        clock,
        Some(Duration::from_millis(20)),
        vec![ORDERBOOK_DEPTH],
    )
    .unwrap();

    // Поток, который уже закончился, — ровно так выглядит шард, чей рантайм
    // вышел: `is_finished()` на тике это и замечает.
    let finished = std::thread::spawn(|| {});
    while !finished.is_finished() {
        std::thread::yield_now();
    }
    assert_eq!(feed.shards.len(), 1, "два инструмента влезают в один сокет");
    let symbols: Vec<u16> = feed.shards[0].specs.iter().map(|s| s.index).collect();
    assert_eq!(symbols.len(), 2, "шард несёт оба инструмента");
    feed.shards[0].handle = Some(finished);

    let first = feed.next_event().expect("тик обязан прийти");
    assert!(matches!(first, Event::Tick { .. }), "{first:?}");
    let mut kinds = Vec::new();
    for _ in 0..symbols.len() {
        match feed
            .next_event()
            .expect("смерть шарда обязана быть видна, а не промолчать")
        {
            Event::Gap { symbol, kind, .. } => kinds.push((symbol, kind)),
            other => panic!("ждали Gap, получили {other:?}"),
        }
    }
    assert_eq!(
        kinds,
        vec![
            (0, GapKind::Disconnected),
            (1, GapKind::DisconnectedSameSocket)
        ],
        "первый инструмент шарда считает переподключение, остальные — только строку"
    );
    assert!(feed.shards[0].handle.is_none(), "отчитались один раз");

    // Следующий тик про того же шарда молчит: повтор на каждом тике залил бы
    // `gaps.csv` строками про одно и то же.
    let second = feed.next_event().expect("тик");
    assert!(matches!(second, Event::Tick { .. }), "{second:?}");
    feed.stop_handle().stop();
}

// -----------------------------------------------------------------------
// A8.1: снятие инструмента с записи на ходу — сокет обязан замолчать, а
// соседи по нему переехать в новый **с теми же индексами**.
// -----------------------------------------------------------------------

/// Транспорт с номером соединения: подписки пишутся в общий журнал с
/// префиксом номера, а закрытие соединения — в журнал закрытий. По ним
/// видно и что старый сокет действительно остановлен, и что оставшийся
/// инструмент получил **новое** соединение, а не продолжил жить на старом.
struct NumberedTransport {
    id: usize,
    inbox: Arc<Mutex<VecDeque<Result<Frame, TransportError>>>>,
    sent: Arc<Mutex<Vec<String>>>,
    closed: Arc<Mutex<Vec<usize>>>,
}

impl Transport for NumberedTransport {
    async fn send_text(&mut self, msg: String) -> Result<(), TransportError> {
        self.sent.lock().unwrap().push(format!("{} {msg}", self.id));
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

impl Drop for NumberedTransport {
    fn drop(&mut self) {
        self.closed.lock().unwrap().push(self.id);
    }
}

/// Ящик кадров одного соединения (тот же тип, что у `ScriptedTransport`) —
/// вынесен в имя, чтобы «очередь ящиков» на соединение читалась как очередь.
type InboxQueue = Arc<Mutex<VecDeque<Result<Frame, TransportError>>>>;

struct NumberedConnector {
    next_id: Arc<std::sync::atomic::AtomicUsize>,
    inboxes: Arc<Mutex<VecDeque<InboxQueue>>>,
    sent: Arc<Mutex<Vec<String>>>,
    closed: Arc<Mutex<Vec<usize>>>,
}

impl TransportConnector for NumberedConnector {
    type Transport = NumberedTransport;
    fn connect(
        &mut self,
    ) -> impl Future<Output = Result<NumberedTransport, TransportError>> + Send {
        let id = self
            .next_id
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let inbox = self
            .inboxes
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| Arc::new(Mutex::new(VecDeque::new())));
        let sent = self.sent.clone();
        let closed = self.closed.clone();
        async move {
            Ok(NumberedTransport {
                id,
                inbox,
                sent,
                closed,
            })
        }
    }
}

/// Соединения в тестах поднимаются своим ОС-потоком: ждём факт по времени, а
/// не по событию канала. Тест проверяет поведение снятия, а не гонку.
fn wait_until(what: &str, mut cond: impl FnMut() -> bool) {
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        if cond() {
            return;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    panic!("не дождались: {what}");
}

fn numbered_feed(
    pool: Vec<PoolMember>,
    sent: Arc<Mutex<Vec<String>>>,
    closed: Arc<Mutex<Vec<usize>>>,
) -> LiveFeed {
    let next_id = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let inboxes = Arc::new(Mutex::new(VecDeque::new()));
    LiveFeed::spawn_with_clock_and_connector_and_ticks(
        pool,
        move |_m| NumberedConnector {
            next_id: next_id.clone(),
            inboxes: inboxes.clone(),
            sent: sent.clone(),
            closed: closed.clone(),
        },
        SystemClock,
        None,
        vec![ORDERBOOK_DEPTH],
    )
    .unwrap()
}

/// Снятие инструмента, делящего сокет с соседом: старый сокет замолкает
/// (соединение закрыто), сосед переезжает в новое соединение **с прежним
/// индексом** и подпиской без снятого топика, а следующий добавленный
/// инструмент получает индекс, продолжающий нумерацию пула, — индексы не
/// переиспользуются (иначе вернувшийся символ писал бы в чужой файл).
#[test]
fn remove_stops_the_socket_and_respawns_the_neighbours_with_the_same_indices() {
    let sent = Arc::new(Mutex::new(Vec::<String>::new()));
    let closed = Arc::new(Mutex::new(Vec::<usize>::new()));
    let mut feed = numbered_feed(
        vec![
            PoolMember {
                symbol: "AAAUSDT".to_string(),
                tick_e9: 1_000_000_000,
                step_e9: 1_000_000_000,
            },
            PoolMember {
                symbol: "BBBUSDT".to_string(),
                tick_e9: 1_000_000_000,
                step_e9: 1_000_000_000,
            },
        ],
        sent.clone(),
        closed.clone(),
    );

    wait_until(
        "первое соединение обязано подписаться",
        || !sent.lock().unwrap().is_empty(),
    );
    assert_eq!(feed.shards.len(), 1, "два инструмента влезают в один сокет");
    assert_eq!(
        feed.shards[0]
            .specs
            .iter()
            .map(|s| s.index)
            .collect::<Vec<_>>(),
        vec![0, 1],
        "шард несёт оба инструмента по порядку пула"
    );
    let first = sent.lock().unwrap().clone();
    assert!(
        first[0].starts_with("0 ") && first[0].contains("AAAUSDT") && first[0].contains("BBBUSDT"),
        "первое соединение подписано на оба топика: {first:?}"
    );

    crate::feed::DynamicPool::remove(&mut feed, &[1]).unwrap();

    wait_until(
        "снятый сокет обязан закрыться",
        || closed.lock().unwrap().contains(&0),
    );
    wait_until(
        "сосед обязан подняться новым соединением",
        || {
            sent.lock()
                .unwrap()
                .iter()
                .any(|m| m.starts_with("1 ") && m.contains("AAAUSDT"))
        },
    );

    assert_eq!(feed.shards.len(), 1, "уцелевший сосед — один шард");
    assert_eq!(
        feed.shards[0]
            .specs
            .iter()
            .map(|s| s.index)
            .collect::<Vec<_>>(),
        vec![0],
        "индекс соседа не сдвинулся: кадр пойдёт в тот же файл"
    );
    assert!(
        feed.shards[0].handle.is_some(),
        "новый шард жив, и о его смерти рапортует V12"
    );
    let sent_now = sent.lock().unwrap().clone();
    let second_subscribe = sent_now
        .iter()
        .find(|m| m.starts_with("1 "))
        .expect("подписка нового соединения")
        .clone();
    assert!(
        !second_subscribe.contains("BBBUSDT"),
        "снятый инструмент в новую подписку не попадает: {second_subscribe}"
    );

    // Индексы не переиспользуются: следующий добавленный получает 2, а не 1.
    let indices = crate::feed::DynamicPool::add(
        &mut feed,
        vec![PoolMember {
            symbol: "CCCUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }],
    )
    .unwrap();
    assert_eq!(
        indices,
        vec![2],
        "номер снятого инструмента не достаётся никому: он ещё в истории сессии"
    );
    feed.stop_handle().stop();
}

/// Снятие единственного инструмента шарда убирает шард целиком: поток ввода-вывода
/// закрыт, подписок не осталось, и снятие неизвестного индекса — no-op, а не
/// паника (индексы живут в истории вызывающего, а не в `Feed`).
#[test]
fn removing_the_last_symbol_of_a_shard_leaves_no_thread_behind() {
    let sent = Arc::new(Mutex::new(Vec::<String>::new()));
    let closed = Arc::new(Mutex::new(Vec::<usize>::new()));
    let mut feed = numbered_feed(
        vec![PoolMember {
            symbol: "AAAUSDT".to_string(),
            tick_e9: 1_000_000_000,
            step_e9: 1_000_000_000,
        }],
        sent.clone(),
        closed.clone(),
    );
    wait_until(
        "соединение обязано подписаться",
        || !sent.lock().unwrap().is_empty(),
    );

    crate::feed::DynamicPool::remove(&mut feed, &[0]).unwrap();
    assert!(feed.shards.is_empty(), "шарду без инструментов жить нечем");
    wait_until(
        "соединение обязано закрыться",
        || closed.lock().unwrap().contains(&0),
    );

    crate::feed::DynamicPool::remove(&mut feed, &[7]).unwrap();
    assert!(
        feed.shards.is_empty(),
        "снятие неизвестного индекса ничего не поднимает и не роняет"
    );
}
