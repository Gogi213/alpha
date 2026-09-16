//! `lob pick` — тонкая сетевая оболочка часового замера (шаг 0.4): держит
//! до `POOL_SIZE` (`super::pool`) живых сокетов одновременно и копит
//! `DepthSample` (`super::depth`) на общем дедлайне до конца окна. Не
//! покрыта тестами без сети по той же причине, по которой её нельзя
//! устроить в CI, — вся логика отбора уже вынесена в чистые функции
//! `super::depth` и `super::pool`.

use std::collections::HashMap;
use std::time::Duration;

use crate::bybit::rest::Instrument;

use super::depth::{
    time_weighted_median_ask_depth_usd_e9, time_weighted_median_bid_depth_usd_e9, DepthSample,
    MeasuredCandidate,
};
use super::pool::PoolCandidate;

/// «Один непрерывный час живого orderbook.50» (Decision 18, шаг 0.4 плана).
pub const MEASUREMENT_WINDOW_SECS: u64 = 3600;

/// Интервал пинга во время часового замера. Не число `PLAN.md` (план не
/// задаёт его нигде) и не измеренная величина — операционный выбор этого
/// файла, тот же по смыслу и по причине, что `bybit::conn::ConnConfig::
/// ping_interval` (см. его doc в `conn.rs`): значение должно укладываться
/// в документированный Bybit таймаут простоя сервера, а конкретное число
/// внутри этого окна конфигурация подключения, не гейт и не порог отчёта.
/// Названная константа в одном месте, а не литерал внутри тела
/// `measure_one_symbol`, — чтобы её было видно и менять, не читая функцию.
const MEASUREMENT_PING_INTERVAL: Duration = Duration::from_secs(20);

/// Бэкофф переподключения во время часового замера — та же природа, что и
/// `MEASUREMENT_PING_INTERVAL` выше: эксплуатационный выбор, не число плана.
const MEASUREMENT_BACKOFF: crate::bybit::conn::BackoffConfig = crate::bybit::conn::BackoffConfig {
    initial: Duration::from_millis(500),
    max: Duration::from_secs(30),
    multiplier: 2,
};

/// Наносекунды от эпохи Unix, через `bybit::conn::SystemClock` — **не** через
/// свой `SystemTime::now().duration_since(UNIX_EPOCH).expect(...)`. Раньше
/// здесь стоял именно такой `.expect(...)`: `conn.rs::SystemClock::ns_since_epoch`
/// документирует ровно этот случай («мёртвая RTC или несконфигурированная VM
/// отдают `SystemTime::now() < UNIX_EPOCH`, и это падало на первом же
/// фрейме — рекордер... гас в первую же секунду работы») как уже однажды
/// исправленный дефект. `lob pick` не имеет права реимплементировать тот же
/// баг под другим именем — переиспользование делает второй экземпляр той же
/// ошибки невозможным по построению, а не только маловероятным.
fn wall_clock_ns() -> i64 {
    use crate::bybit::conn::Clock;
    crate::bybit::conn::SystemClock.now_ns()
}

/// `pub(super)`: `run_pick_async` (родительский `mod.rs`) берёт «сейчас» для
/// `build_pool` этим же вызовом — вторая копия часов недопустима тем же
/// приёмом, что описан в doc `wall_clock_ns` выше.
pub(super) fn wall_clock_ms() -> i64 {
    wall_clock_ns() / 1_000_000
}

/// Номинал одного уровня книги в USD·1e9, целиком в целых (A1): `tick_e9` и
/// `step_e9` — те же масштабы, что уже используются для подключения к этому
/// символу (`bybit::conn::ConnConfig`), поэтому передаются вызывающим, а не
/// читаются из `Book` — у неё нет публичного геттера этих полей, и заводить
/// его здесь означало бы менять чужой файл (`book/mod.rs`) ради этой оболочки.
/// Итоговый каст точен: частное — номинал долларового порядка (~1e9–1e15 e9);
/// пределы `i128` промежуточных шагов разобраны в `super::depth::event_rate_cmp`.
#[allow(clippy::cast_possible_truncation)]
fn level_notional_usd_e9(tick: i64, qty_lots: i64, tick_e9: i64, step_e9: i64) -> i64 {
    let price_e9 = tick as i128 * tick_e9 as i128;
    let qty_e9 = qty_lots as i128 * step_e9 as i128;
    // Оба множителя уже в масштабе 1e9; их произведение — в 1e18, обратно
    // к 1e9 делением на 1e9. Без плавающей точки: см. doc модуля A1 и
    // комментарий `event_rate_cmp` про пределы `i128`.
    (price_e9 * qty_e9 / 1_000_000_000) as i64
}

/// Номинал каждого уровня **одной стороны** книги в USD·1e9, одним вектором
/// и одной аллокацией: `Book::levels` отдаёт `Map<Range<usize>, _>`, чей
/// `size_hint` точен, и `collect` резервирует нужный размер заранее.
///
/// Раньше эта функция принимала обе стороны разом (`Iterator::chain` бид с
/// аском в один вектор) — Decision 18(б), ревизия 10, запрещает объединять
/// стороны: порог глубины обязан проверяться на каждой независимо (см. doc
/// `DepthSample`), и общий вектор для этого уже не годится. По одному вызову
/// на сторону на каждое принятое обновление книги — на часовом замере с
/// `POOL_SIZE` параллельными символами это не косметика: событие
/// книги — самый частый код этого модуля.
fn book_level_notionals_usd_e9(
    book: &crate::book::Book,
    side: crate::book::Side,
    tick_e9: i64,
    step_e9: i64,
) -> Vec<i64> {
    book.levels(side)
        .map(|(tick, qty_lots)| level_notional_usd_e9(tick, qty_lots, tick_e9, step_e9))
        .collect()
}

/// Итог замера одного символа: замеры глубины книги (как раньше) плюс лоты
/// неблочных сделок ленты — вход медианы размера сделки (план D-H3, таск
/// 08). Одно соединение на оба: `Connection::run` уже подписывает разом
/// `orderbook.50` и `publicTrade` (`conn.rs`), второго канала заводить не
/// нужно — то самое «можно тем же окном, что замер глубины» из таска.
#[derive(Debug, Default)]
struct SymbolMeasurement {
    depth_samples: Vec<DepthSample>,
    /// Блочные (`BT`) исключены — они не проедают видимую книгу и не годятся
    /// мерить типичный размер потока, та же причина, что исключает их из
    /// разметки уровней (`lob/levels.rs`).
    trade_lots: Vec<i64>,
}

/// Лоты одной сделки для медианы размера сделки (план D-H3, таск 08) —
/// `None` для блочных: блочная сделка не проедает видимую книгу и не годится
/// мерить типичный размер потока, та же причина, что исключает их из
/// разметки уровней (`lob/levels.rs`). Масштаб — тот же перевод decimal→лоты,
/// что `book::Book::to_lots` уже делает для книги (A1); здесь не через
/// `Result`, потому что не хот-путь и кандидат с кривым размером сделки
/// пропускается молча, а не роняет часовой замер.
fn trade_lots_for_median(trade: &crate::bybit::ws::Trade, step_e9: i64) -> Option<i64> {
    if trade.block {
        None
    } else {
        Some(trade.qty_e9 / step_e9)
    }
}

/// Один символ: подключается, копит замеры глубины и лоты сделок до
/// `deadline`, отдаёт их оболочке выше для усреднения по времени и медианы.
/// Разрыв последовательности `u` внутри `bybit::conn::Connection` уже
/// разрешается ресинком самим соединением — здесь достаточно применять
/// только успешные апдейты книги.
///
/// `deadline` — монотонный `Instant`, посчитанный **один раз** вызывающим
/// (`measure_prefiltered`) до того, как запущен хоть один символ, а не
/// `Instant::now() + duration` внутри этой функции: до `POOL_SIZE`
/// задач планируются и подключаются не одновременно (обычный джиттер
/// планировщика `tokio`, плюс у каждой свой бэкофф переподключения), и
/// пересчёт с нуля внутри каждой задачи растягивал бы дедлайн именно этого
/// символа на величину этой задержки — молча, без ошибки, без следа в
/// коммитимой таблице. Один и тот же `Instant` для всех задач держит их на
/// общей временной шкале с `window_end_ns`, которым дальше взвешивается
/// последний замер каждого символа (`time_weighted_median_bid_depth_usd_e9`,
/// `time_weighted_median_ask_depth_usd_e9`).
async fn measure_one_symbol(
    symbol: String,
    tick_e9: i64,
    step_e9: i64,
    deadline: tokio::time::Instant,
) -> SymbolMeasurement {
    use crate::book::{Book, Side};
    use crate::bybit::conn::{
        BybitPublicLinearConnector, ConnConfig, ConnEvent, Connection, SystemClock,
    };
    use crate::bybit::ws::Event;

    let cfg = ConnConfig {
        symbol,
        tick_e9,
        step_e9,
        ping_interval: MEASUREMENT_PING_INTERVAL,
        backoff: MEASUREMENT_BACKOFF,
    };
    let (tx, mut rx) = tokio::sync::mpsc::channel::<ConnEvent>(4096);
    let conn = Connection::new(BybitPublicLinearConnector, cfg);
    let conn_task = tokio::spawn(conn.run(SystemClock, tx));

    let mut book = Book::new(tick_e9, step_e9);
    let mut out = SymbolMeasurement::default();

    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, rx.recv()).await {
            Ok(Some(ConnEvent::Message {
                local_ts_ns,
                event: Event::Book(update),
                ..
            })) => {
                if book.apply(&update).is_ok() {
                    out.depth_samples.push(DepthSample {
                        at_ns: local_ts_ns,
                        bid_notional_usd_e9: book_level_notionals_usd_e9(
                            &book,
                            Side::Bid,
                            tick_e9,
                            step_e9,
                        ),
                        ask_notional_usd_e9: book_level_notionals_usd_e9(
                            &book,
                            Side::Ask,
                            tick_e9,
                            step_e9,
                        ),
                    });
                }
            }
            Ok(Some(ConnEvent::Message {
                event: Event::Trade(trade),
                ..
            })) => {
                if let Some(lots) = trade_lots_for_median(&trade, step_e9) {
                    out.trade_lots.push(lots);
                }
            }
            Ok(Some(_)) => {}
            Ok(None) => break,
            Err(_) => break, // истекло `remaining` — окно замера кончилось
        }
    }

    conn_task.abort();
    out
}

/// Конец окна замера в тех же наносекундах. Сумма точна до 2262 года:
/// старт ~1.7e18 плюс окно часового порядка против `i64::MAX` ~9.2e18.
#[allow(clippy::cast_possible_truncation)]
fn window_end_ns(window_start_ns: i64, duration: Duration) -> i64 {
    window_start_ns + duration.as_nanos() as i64
}

/// Замеряет все символы пула одновременно (по соединению на инструмент —
/// поэтому размер пула задаётся `--top`, а не «все листинги разом»; лимит
/// топиков на соединение здесь не упирается) в течение одного и того же часа,
/// а не по очереди.
pub async fn measure_prefiltered(
    prefiltered: &[PoolCandidate],
    instruments_by_symbol: &HashMap<String, &Instrument>,
    window_secs: u64,
) -> Vec<MeasuredCandidate> {
    let window_start_utc_ms = wall_clock_ms();
    let window_start_ns = wall_clock_ns();
    let duration = Duration::from_secs(window_secs);
    let window_end_ns = window_end_ns(window_start_ns, duration);
    // Один и тот же дедлайн для всех символов — см. doc `measure_one_symbol`
    // про то, почему он не пересчитывается внутри каждой задачи.
    let deadline = tokio::time::Instant::now() + duration;

    let mut handles = Vec::with_capacity(prefiltered.len());
    for cand in prefiltered {
        let Some(inst) = instruments_by_symbol.get(&cand.symbol) else {
            continue;
        };
        handles.push((
            cand.symbol.clone(),
            cand.turnover_24h_usd_e9,
            tokio::spawn(measure_one_symbol(
                cand.symbol.clone(),
                inst.tick_e9,
                inst.qty_step_e9,
                deadline,
            )),
        ));
    }

    let mut out = Vec::new();
    for (symbol, reported_turnover_usd_e9, handle) in handles {
        let Ok(measurement) = handle.await else {
            continue;
        };
        let samples = measurement.depth_samples;
        // Обе стороны обязаны иметь измерение (Decision 18(б)) — кандидат
        // без данных на какой-либо из сторон пропускается целиком, так же
        // как раньше пропускался кандидат без единого объединённого числа.
        let Some(median_bid_depth_usd_e9) =
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns)
        else {
            continue;
        };
        let Some(median_ask_depth_usd_e9) =
            time_weighted_median_ask_depth_usd_e9(&samples, window_end_ns)
        else {
            continue;
        };
        // Переиспользует ту же реализацию медианы, что и глубина
        // (`super::depth::median_depth_per_level_usd_e9`): величина другая
        // (лоты, не USD·1e9), формула та же (сортировка, средний элемент
        // либо среднее двух средних) — второй реализации не заводим.
        let median_trade_lots =
            super::depth::median_depth_per_level_usd_e9(&measurement.trade_lots);
        out.push(MeasuredCandidate {
            symbol,
            window_start_utc_ms,
            window_secs: window_secs as i64,
            events: samples.len() as i64,
            median_bid_depth_usd_e9,
            median_ask_depth_usd_e9,
            reported_turnover_usd_e9,
            median_trade_lots,
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // -- trade_lots_for_median (план D-H3, таск 08) --------------------------

    fn trade(qty_e9: i64, block: bool) -> crate::bybit::ws::Trade {
        crate::bybit::ws::Trade {
            exch_ms: 0,
            price_e9: 100_000_000_000,
            qty_e9,
            aggressor_is_buy: true,
            block,
            rpi: false,
        }
    }

    #[test]
    fn trade_lots_for_median_converts_decimal_qty_to_lots() {
        // 1.5 базового актива при шаге 0.1 — пятнадцать лотов.
        assert_eq!(
            trade_lots_for_median(&trade(1_500_000_000, false), 100_000_000),
            Some(15)
        );
    }

    #[test]
    fn trade_lots_for_median_excludes_block_trades() {
        assert_eq!(
            trade_lots_for_median(&trade(1_500_000_000, true), 100_000_000),
            None,
            "блочная сделка не проедает видимую книгу — не вход медианы"
        );
    }

    // -- wall_clock_ns/wall_clock_ms — не переизобретают исправленную панику --

    /// Регрессия: здесь раньше стоял `SystemTime::now().duration_since(
    /// UNIX_EPOCH).expect(...)` — ровно тот дефект, который
    /// `bybit::conn::SystemClock::ns_since_epoch` уже один раз исправил (см.
    /// его doc: мёртвая RTC или несконфигурированная VM отдают время раньше
    /// эпохи, и `.expect()` на этом падает на первом же вызове). Грепает
    /// исходник этого же файла — тот же приём, что `ARCHITECTURE.md` уже
    /// использует для границы `signal/`/`strategy/` («это проверяется
    /// тестом, который грепает дерево модулей, а не дисциплиной»): свойство
    /// «здесь нет .expect() на системных часах» не выразить иначе без
    /// инъекции часов, которой у `wall_clock_ns` по конструкции нет.
    #[test]
    fn wall_clock_does_not_reintroduce_the_fixed_unix_epoch_panic() {
        let source = include_str!("measure.rs");
        assert!(
            !source.contains(".expect(\"системные часы обязаны быть после эпохи Unix\")"),
            "wall_clock_ns/wall_clock_ms обязаны брать время через \
             bybit::conn::SystemClock::now_ns() (не паникующий), а не через \
             собственный SystemTime::now().duration_since(UNIX_EPOCH).expect(...)"
        );
    }

    // -- book_level_notionals_usd_e9 — по одной аллокации на сторону --------

    /// Требуемый тест (Изменение 1): раньше эта функция принимала обе
    /// стороны разом (`Iterator::chain` бид+аск в один вектор) — Decision
    /// 18(б), ревизия 10, запрещает объединять стороны, и функция теперь
    /// читает ровно одну. `alloc_count::measure` — тот же счётчик, что уже
    /// используют `stats/mod.rs::
    /// the_replication_loop_does_not_allocate_per_replicate`,
    /// `bybit::clock.rs` и `binlog/mod.rs`: он бы не заметил регресс до
    /// `Vec::extend` или до чтения не той стороны, если бы измерялось
    /// только количество элементов.
    #[test]
    fn book_level_notionals_usd_e9_reads_only_the_requested_side_with_one_allocation() {
        use crate::book::{Book, Side, Update};

        let mut book = Book::new(1, 1);
        book.apply(&Update {
            is_snapshot: true,
            depth: 50,
            u: 1,
            seq: 1,
            cts_ms: 0,
            bids: vec![(100, 5), (99, 3), (98, 1)],
            asks: vec![(101, 2), (102, 4)],
        })
        .unwrap();

        let (bid_levels, bid_counts) =
            crate::alloc_count::measure(|| book_level_notionals_usd_e9(&book, Side::Bid, 1, 1));
        assert_eq!(bid_levels.len(), 3, "три уровня бида, ни одного аска");
        assert_eq!(bid_counts.allocations, 1);

        let (ask_levels, ask_counts) =
            crate::alloc_count::measure(|| book_level_notionals_usd_e9(&book, Side::Ask, 1, 1));
        assert_eq!(ask_levels.len(), 2, "два уровня аска, ни одного бида");
        assert_eq!(ask_counts.allocations, 1);
    }

    // -- measure_one_symbol — общий дедлайн, не Instant::now() внутри задачи -

    /// Требуемый тест: без сети, но не тавтологический — гоняет настоящую
    /// `measure_one_symbol` с настоящим `BybitPublicLinearConnector` (сеть
    /// нужна только внутри отдельно заспавненной задачи `conn.run(...)`,
    /// которую эта функция никогда не ждёт до своего собственного дедлайна).
    /// Симулирует ровно сценарий finding'а: задача добралась до цикла позже,
    /// чем был посчитан дедлайн (здесь — позже самого дедлайна). Старая
    /// реализация принимала `duration: Duration` и считала `Instant::now() +
    /// duration` заново при каждом вызове — с ней это же обращение
    /// проработало бы ещё почти полный `duration`, а не вернулось сразу.
    ///
    /// Изменение 4: сам вызов обёрнут в `tokio::time::timeout`, а не голый
    /// `.await`. Регрессия («дедлайн игнорируется и считается заново от
    /// текущего момента») заставляет `measure_one_symbol` блокироваться на
    /// `rx.recv()` без сети и без собственного ограничения по времени —
    /// без внешнего `timeout` тест тогда не падает красным, а виснет, и CI
    /// читает зависший тест как таймаут инфраструктуры (обычный ответ —
    /// ретрай), а не как красный ассерт. Бюджет — секунды, с большим
    /// запасом над тем, что нужно правильному коду (он обязан вернуться
    /// немедленно), но много меньше часа, на который способна растянуть
    /// ожидание регрессия при реальном `MEASUREMENT_WINDOW_SECS`.
    #[tokio::test]
    async fn measure_one_symbol_stops_at_the_shared_deadline_even_if_started_late() {
        let deadline = tokio::time::Instant::now() + Duration::from_millis(40);
        // «Поздний старт»: тем временем, что делит все символы одного
        // замера, уже распорядились — деконнект, бэкофф, планировщик.
        tokio::time::sleep(Duration::from_millis(80)).await;

        let began = tokio::time::Instant::now();
        let result = tokio::time::timeout(
            Duration::from_secs(2),
            measure_one_symbol("SOLUSDT".to_string(), 1, 1, deadline),
        )
        .await;
        let elapsed = began.elapsed();

        let measurement = result.expect(
            "measure_one_symbol обязана вернуться немедленно на уже истёкшем \
             дедлайне, а не блокироваться на rx.recv() без таймаута — таймаут \
             этого теста истёк первым, что и есть регрессия, которую он ловит",
        );

        assert!(
            measurement.depth_samples.is_empty() && measurement.trade_lots.is_empty(),
            "дедлайн уже в прошлом на момент вызова — цикл не должен успеть ни одного замера"
        );
        assert!(
            elapsed < Duration::from_millis(30),
            "функция обязана вернуться немедленно, если переданный дедлайн уже \
             в прошлом, а не отсчитывать duration заново от своего собственного \
             старта — заняло {elapsed:?}"
        );
    }
}
