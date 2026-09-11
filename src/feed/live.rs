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
    BackoffConfig, BybitPublicLinearConnector, ConnConfig, ConnEvent, Connection, SystemClock,
    TransportConnector,
};

use super::{Event, Feed};

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

/// Живой `Feed`: одно подключение на инструмент пула, один общий канал в
/// поток решения. `next_event` блокирует поток решения на канале
/// (`blocking_recv`) — ровно то разделение, которое `ARCHITECTURE.md` A3
/// называет «один поток решений, ввод-вывод отдельно».
pub struct LiveFeed {
    rx: mpsc::Receiver<(u8, ConnEvent)>,
    _io_thread: std::thread::JoinHandle<()>,
}

impl LiveFeed {
    /// Продовый вход: одно подключение на инструмент, реальный сокет.
    pub fn spawn(pool: Vec<PoolMember>) -> Self {
        Self::spawn_with(pool, |_member| BybitPublicLinearConnector)
    }

    /// Обобщённый вход (шов теста 1, `interfaces.md`: `Transport` —
    /// фейковый сокет). `make_connector` вызывается один раз на инструмент,
    /// на старте — как и продовый путь, который каждому `Connection` даёт
    /// свой `BybitPublicLinearConnector`.
    pub fn spawn_with<C, F>(pool: Vec<PoolMember>, mut make_connector: F) -> Self
    where
        C: TransportConnector + 'static,
        F: FnMut(&PoolMember) -> C,
    {
        print_topic_budget(&pool);
        let capacity = CHANNEL_CAPACITY_PER_SYMBOL.saturating_mul(pool.len().max(1));
        let (tx, rx) = mpsc::channel::<(u8, ConnEvent)>(capacity);
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
                    let (conn_tx, mut conn_rx) =
                        mpsc::channel::<ConnEvent>(CHANNEL_CAPACITY_PER_SYMBOL);
                    let out = tx.clone();
                    tasks.push(tokio::spawn(async move {
                        while let Some(ev) = conn_rx.recv().await {
                            if out.send((idx, ev)).await.is_err() {
                                break;
                            }
                        }
                    }));
                    let conn = Connection::new(connector, cfg);
                    tasks.push(tokio::spawn(conn.run(SystemClock, conn_tx)));
                }
                drop(tx);
                for t in tasks {
                    let _ = t.await;
                }
            });
        });

        Self {
            rx,
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
        let (idx, conn_event) = self.rx.blocking_recv()?;
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
                detail: format!("кадр не разобрался: {err:?}"),
            },
            ConnEvent::SequenceGap { expected, got } => Event::Gap {
                symbol: idx,
                local_ts_ns: 0,
                detail: format!("разрыв u: ждали {expected}, пришло {got}"),
            },
            ConnEvent::BookInvariantViolated { err } => Event::Gap {
                symbol: idx,
                local_ts_ns: 0,
                detail: format!("книга нарушена: {err:?}"),
            },
            ConnEvent::Disconnected => Event::Gap {
                symbol: idx,
                local_ts_ns: 0,
                detail: "сокет переподключился".to_string(),
            },
        })
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
}
