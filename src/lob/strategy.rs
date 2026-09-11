//! Стратегия — одна функция `on_event<MD, B: Bot<MD>>` (A6, D-СТРАТЕГИЯ,
//! таск 15). Идёт в `Backtest` крейта (`lob/backtest.rs:506`, шаг 6.3) и, в
//! фазе 2, в `LiveBot` — без единой правки: обе стороны реализуют один и тот
//! же `hftbacktest::types::Bot<MD>`, и это то самое A6 «стратегия пишется под
//! `Bot<MD>` крейта».
//!
//! Экономические примитивы не переизобретены здесь второй раз — это была бы
//! Reinvention (`interfaces.md`, правило 5): вход мейкером у своей стороны
//! спреда, время жизни входа, горизонт выхода и знак стороны уже названы и
//! проверены в `lob::backtest` (`entry_side`, `entry_price`, `exit_price`,
//! `ENTRY_TTL_NS`, `HOLD_NS`, `SIGMA_LONG`/`SIGMA_SHORT` — все `pub`), и этот
//! файл их переиспользует как есть. Разница с `backtest::drive_cell` — не в
//! экономике сделки, а в форме: `drive_cell` управляет временем бэктеста
//! пакетно, по заранее известному списку сигналов (`&[Signal]`), и не может
//! быть вызвана на живом сокете, где будущих событий не существует.
//! `on_event` — ровно одно событие за вызов, без `elapse`/`wait_*` внутри
//! себя: кто и когда продвигает часы стороны — решает вызывающий (тест ниже
//! и, в фазе 2, реальный live-цикл), а не эта функция.
//!
//! `best_prices` ниже — небольшая копия одноимённого приватного хелпера
//! `lob::backtest` (шесть строк: две проверки `INVALID_MIN`/`INVALID_MAX`),
//! а не импорт: там он не публичный, а вынесение его наружу — правка чужого
//! файла (`backtest.rs`, зона таска 11), не разрешённая этим тикетом.

use hftbacktest::depth::{MarketDepth, INVALID_MAX, INVALID_MIN};
use hftbacktest::types::{Bot, OrdType, Side as HbtSide, TimeInForce};

use crate::lob::backtest::{entry_price, entry_side, exit_price, ENTRY_TTL_NS, HOLD_NS};

/// Копия `backtest::best_prices` (приватна там же, см. doc модуля выше):
/// лучшие цены стороны как `(bid, ask)`, `None` — книга неполна.
fn best_prices<MD: MarketDepth>(depth: &MD) -> Option<(f64, f64)> {
    if depth.best_bid_tick() == INVALID_MIN || depth.best_ask_tick() == INVALID_MAX {
        return None;
    }
    let (bid, ask) = (depth.best_bid(), depth.best_ask());
    if bid.is_finite() && ask.is_finite() && bid > 0.0 && ask > 0.0 {
        Some((bid, ask))
    } else {
        None
    }
}

/// Фаза одного круга. Спрятана от вызывающего (`interfaces.md`: модуль
/// `lob/strategy` «прячет: триггер, состояние») — снаружи виден только
/// `Action`, возвращённый из `on_event`.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Phase {
    /// Ждём случая войти: следующий валидный тик со спредом — и есть
    /// триггер (D-ОРДЕР: цена и размер к этому моменту уже известны заранее,
    /// сборка кадра на живом пути — забота `commands::lob::react`, не эта
    /// функция — здесь только решение `Bot<MD>`).
    Idle,
    /// Вход отправлен, ждём исполнения или истечения `ENTRY_TTL_NS`.
    EntryPending { order_id: u64, sent_ns: i64 },
    /// Позиция открыта, ждём `HOLD_NS` до выхода.
    Holding { entry_ns: i64 },
    /// Выход отправлен, ждём, когда позиция обнулится.
    ExitPending { order_id: u64 },
}

/// Состояние одного круга одной стратегии на одном активе. `sigma` — сторона
/// этого круга (`SIGMA_LONG`/`SIGMA_SHORT`, `backtest::entry_side`),
/// назначается снаружи один раз при вооружении: какой именно сигнал
/// ловить — решение уровня `lob/levels`, не этого модуля (границы модулей,
/// `interfaces.md`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StrategyState {
    asset_no: usize,
    sigma: i8,
    qty: f64,
    next_order_id: u64,
    phase: Phase,
}

impl StrategyState {
    pub fn new(asset_no: usize, sigma: i8, qty: f64, first_order_id: u64) -> Self {
        Self {
            asset_no,
            sigma,
            qty,
            next_order_id: first_order_id,
            phase: Phase::Idle,
        }
    }

    pub fn is_idle(&self) -> bool {
        matches!(self.phase, Phase::Idle)
    }

    fn take_order_id(&mut self) -> u64 {
        let id = self.next_order_id;
        self.next_order_id = self.next_order_id.saturating_add(1);
        id
    }
}

/// Что сделал последний вызов `on_event`. `Idle` — ничего не произошло на
/// этом событии (ждём книгу, ждём таймаут, ждём исполнения) — самый частый
/// исход на живом потоке, где событий много, а решений мало.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Action {
    Idle,
    EntrySubmitted {
        order_id: u64,
        side: HbtSide,
        price: f64,
    },
    EntryTimedOut {
        order_id: u64,
    },
    ExitSubmitted {
        order_id: u64,
        side: HbtSide,
        price: f64,
    },
}

/// Одна функция стратегии (A6, D-СТРАТЕГИЯ). Вызывается один раз на событие
/// потока — какое именно событие произошло, эта функция не спрашивает: она
/// смотрит на `bot.current_timestamp()`/`bot.depth()`/`bot.position()` в
/// момент вызова, что и есть контракт `Bot<MD>` что в `Backtest`, что в
/// `LiveBot` (`current_timestamp` — время внутри данных или локальные часы
/// соответственно, `ARCHITECTURE.md` A6).
///
/// Не аллоцирует (`ARCHITECTURE.md`, запрет 1): никаких `Vec`/`String` на
/// своём пути, только копируемые `Phase`/`Action` и вызовы `Bot<MD>`.
/// Не вызывает часы напрямую (запрет 2 `interfaces.md`) — время только через
/// `bot.current_timestamp()`. Не делает REST и не строит/подписывает ордер
/// после решения (запреты 4, 5) — `submit_*_order` крейта не имеет отношения
/// к `bybit::sign`, это ответственность `Bot<MD>` (бэктест — симуляция,
/// живой — коннектор фазы 2, вне этого прохода).
pub fn on_event<MD, B>(bot: &mut B, state: &mut StrategyState) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let now = bot.current_timestamp();
    match state.phase {
        Phase::Holding { entry_ns } => {
            if now.saturating_sub(entry_ns) < HOLD_NS {
                return Ok(Action::Idle);
            }
            let Some((bid, ask)) = best_prices(bot.depth(state.asset_no)) else {
                // Без книги выйти нельзя — круг остаётся Holding к следующему
                // событию, а не теряется молча.
                return Ok(Action::Idle);
            };
            let Some(entry_side) = entry_side(state.sigma) else {
                return Ok(Action::Idle);
            };
            let exit_side = match entry_side {
                HbtSide::Buy => HbtSide::Sell,
                _ => HbtSide::Buy,
            };
            let Some(px) = exit_price(entry_side, bid, ask) else {
                return Ok(Action::Idle);
            };
            let order_id = state.take_order_id();
            match exit_side {
                HbtSide::Buy => {
                    bot.submit_buy_order(
                        state.asset_no,
                        order_id,
                        px,
                        state.qty,
                        TimeInForce::GTC,
                        OrdType::Limit,
                        false,
                    )?;
                }
                _ => {
                    bot.submit_sell_order(
                        state.asset_no,
                        order_id,
                        px,
                        state.qty,
                        TimeInForce::GTC,
                        OrdType::Limit,
                        false,
                    )?;
                }
            }
            // Пересекающий лимит вправе исполниться синхронно с отправкой
            // (крейт возвращает управление уже после матчинга) — проверяем
            // позицию сразу, а не откладываем до следующего события, иначе
            // круг завис бы в `ExitPending` навсегда там, где фид на этом
            // и заканчивается.
            state.phase = if bot.position(state.asset_no) == 0.0 {
                Phase::Idle
            } else {
                Phase::ExitPending { order_id }
            };
            Ok(Action::ExitSubmitted {
                order_id,
                side: exit_side,
                price: px,
            })
        }
        Phase::ExitPending { .. } => {
            if bot.position(state.asset_no) == 0.0 {
                state.phase = Phase::Idle;
            }
            Ok(Action::Idle)
        }
        Phase::EntryPending { order_id, sent_ns } => {
            if bot.position(state.asset_no) != 0.0 {
                state.phase = Phase::Holding { entry_ns: now };
                return Ok(Action::Idle);
            }
            if now.saturating_sub(sent_ns) >= ENTRY_TTL_NS {
                bot.cancel(state.asset_no, order_id, false)?;
                state.phase = Phase::Idle;
                return Ok(Action::EntryTimedOut { order_id });
            }
            Ok(Action::Idle)
        }
        Phase::Idle => {
            let Some(side) = entry_side(state.sigma) else {
                return Ok(Action::Idle);
            };
            let Some((bid, ask)) = best_prices(bot.depth(state.asset_no)) else {
                return Ok(Action::Idle);
            };
            let Some(px) = entry_price(side, bid, ask) else {
                return Ok(Action::Idle);
            };
            let order_id = state.take_order_id();
            match side {
                HbtSide::Buy => {
                    bot.submit_buy_order(
                        state.asset_no,
                        order_id,
                        px,
                        state.qty,
                        TimeInForce::GTX,
                        OrdType::Limit,
                        false,
                    )?;
                }
                _ => {
                    bot.submit_sell_order(
                        state.asset_no,
                        order_id,
                        px,
                        state.qty,
                        TimeInForce::GTX,
                        OrdType::Limit,
                        false,
                    )?;
                }
            }
            state.phase = Phase::EntryPending {
                order_id,
                sent_ns: now,
            };
            Ok(Action::EntrySubmitted {
                order_id,
                side,
                price: px,
            })
        }
    }
}

// -----------------------------------------------------------------------
// Грепом по образцу `levels.rs`: горячий путь не вправе звать часы напрямую.
// -----------------------------------------------------------------------

#[cfg(test)]
mod hot_path_guard {
    /// Долг ревью таска 04 применительно к этому файлу: вызов часов напрямую
    /// в горячем пути — ноль (`interfaces.md`, запрет 2), и
    /// это проверяется грепом по собственному исходнику, как `levels.rs`
    /// проверяет себя (`levels.rs::module_stays_detached_from_transport_
    /// clocks_and_approx_numbers`). Строки собраны из частей — иначе
    /// литерал триггерил бы проверку сам на себя.
    #[test]
    fn module_never_calls_the_wall_clock_directly() {
        const SRC: &str = include_str!("strategy.rs");
        let banned = [concat!("Inst", "ant::now"), concat!("System", "Time::now")];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lob::backtest::{latency_from_rtt, SIGMA_LONG};

    use hftbacktest::backtest::assettype::LinearAsset;
    use hftbacktest::backtest::data::Data;
    use hftbacktest::backtest::models::{
        CommonFees, ConstantLatency, RiskAdverseQueueModel, TradingValueFeeModel,
    };
    use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
    use hftbacktest::depth::HashMapMarketDepth;
    use hftbacktest::types::{
        ElapseResult, Event, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT,
        EXCH_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT,
        LOCAL_BUY_TRADE_EVENT, LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
    };

    // Синтетический поток и билдер `Backtest` — та же форма, что
    // `lob::backtest`'s own test module (шов 6 требует настоящий крейтовый
    // `Backtest`, а не второй фейковый `Bot<MD>`); скопировано намеренно —
    // это тестовая обвязка, не стратегия, и дублирование здесь не Reinvention
    // (`interfaces.md`, правило 5 — про стратегию, не про сборку фикстуры).

    fn depth_ev(bid: bool) -> u64 {
        if bid {
            LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
        } else {
            LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
        }
    }

    fn trade_ev(sell: bool) -> u64 {
        if sell {
            LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
        } else {
            LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
        }
    }

    fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> Event {
        Event {
            ev: depth_ev(bid),
            exch_ts,
            local_ts: exch_ts + 500,
            px,
            qty,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> Event {
        Event {
            ev: trade_ev(sell) | EXCH_EVENT | LOCAL_EVENT,
            exch_ts,
            local_ts: exch_ts + 500,
            px,
            qty,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    fn seam6_backtest(feed: &[Event]) -> Backtest<HashMapMarketDepth> {
        let (entry, response) = latency_from_rtt(1_000_000);
        Backtest::builder()
            .add_asset(
                L2AssetBuilder::default()
                    .data(vec![DataSource::Data(Data::from_data(feed))])
                    .latency_model(ConstantLatency::new(entry, response))
                    .asset_type(LinearAsset::new(1.0))
                    .fee_model(TradingValueFeeModel::new(CommonFees::new(0.0002, 0.00055)))
                    .queue_model(RiskAdverseQueueModel::new())
                    .exchange(ExchangeKind::NoPartialFillExchange)
                    .depth(|| HashMapMarketDepth::new(1.0, 1.0))
                    .build()
                    .unwrap(),
            )
            .build()
            .unwrap()
    }

    const S: i64 = 1_000_000_000;

    /// Прогоняет `on_event` в цикле `elapse(шаг) -> on_event` до конца
    /// синтетического фида — тот самый "прогон на `Backtest` крейта через
    /// шов 6", который требует критерий приёмки. `on_event` сама не зовёт
    /// `elapse` (doc модуля) — это работа вызывающего, здесь тестового цикла.
    fn drive(hbt: &mut Backtest<HashMapMarketDepth>, state: &mut StrategyState) -> Vec<Action> {
        let mut actions = Vec::new();
        loop {
            let r = hbt.elapse(100_000_000).unwrap();
            actions.push(on_event(hbt, state).unwrap());
            if r == ElapseResult::EndOfData {
                break;
            }
        }
        actions
    }

    /// Круг целиком через настоящий крейтовый `Backtest`: вход мейкером на
    /// бид, очередь съедена сделками за 2 с, выход тейкером ровно на
    /// `t₀ + HOLD_NS`. Доказывает шов 6 — `on_event<MD, B: Bot<MD>>` работает
    /// без единой правки на реализации `Bot<MD>` из `hftbacktest`, не на
    /// собственном фейке.
    #[test]
    fn on_event_drives_a_full_maker_round_trip_through_the_crates_backtest() {
        let feed = [
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            trade_at(S + S / 2, true, 100.0, 3.0),
            trade_at(S + 4 * S / 5, true, 100.0, 3.0),
            // Спред жив и после горизонта выхода — иначе выйти не на чем.
            depth_at(HOLD_NS + S + 9 * S / 10, true, 101.0, 5.0),
            depth_at(HOLD_NS + S + 9 * S / 10, false, 102.0, 5.0),
            // Хвост с запасом, чтобы ответ на выход успел дойти.
            depth_at(HOLD_NS + 3 * S, false, 103.0, 5.0),
        ];
        let mut hbt = seam6_backtest(&feed);
        let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);

        let actions = drive(&mut hbt, &mut state);

        assert!(
            actions
                .iter()
                .any(|a| matches!(a, Action::EntrySubmitted { .. })),
            "вход обязан был отправиться: {actions:?}"
        );
        assert!(
            actions
                .iter()
                .any(|a| matches!(a, Action::ExitSubmitted { .. })),
            "выход обязан был отправиться: {actions:?}"
        );
        let entry_at = actions
            .iter()
            .position(|a| matches!(a, Action::EntrySubmitted { .. }))
            .expect("проверено выше");
        let exit_at = actions
            .iter()
            .position(|a| matches!(a, Action::ExitSubmitted { .. }))
            .expect("проверено выше");
        assert!(
            entry_at < exit_at,
            "выход обязан идти после входа: {actions:?}"
        );
        assert_eq!(
            hbt.position(0),
            0.0,
            "позиция плоская после выхода — доказательство, что круг прошёл через настоящий Bot<MD>"
        );
        // Круг сам перевооружается на следующий вход, как только книга снова
        // валидна (`Phase::Idle`, D-СТРАТЕГИЯ: система готова ловить сигнал
        // на каждом валидном тике) — состояние после первого полного круга
        // поэтому не обязано быть `Idle`, это уже второй круг в работе. Само
        // перевооружение видно по `EntrySubmitted` после первого `ExitSubmitted`.
        assert!(
            actions[exit_at + 1..]
                .iter()
                .any(|a| matches!(a, Action::EntrySubmitted { .. })),
            "после выхода круг обязан перевооружиться на новый вход: {actions:?}"
        );
    }

    /// Без сделок очередь не двигается: вход снимается через `ENTRY_TTL_NS`
    /// и `on_event` сообщает об этом явным действием, а не молчанием.
    #[test]
    fn on_event_times_out_an_entry_that_never_fills() {
        let feed = [
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            depth_at(10 * S, false, 102.0, 5.0),
        ];
        let mut hbt = seam6_backtest(&feed);
        let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);

        let actions = drive(&mut hbt, &mut state);

        assert!(
            actions
                .iter()
                .any(|a| matches!(a, Action::EntryTimedOut { .. })),
            "неисполнившийся вход обязан истечь явным действием: {actions:?}"
        );
        assert_eq!(hbt.position(0), 0.0, "позиции не было и нет");
    }

    /// Запрет 1 `interfaces.md`: ноль аллокаций на событие после прогрева.
    /// Считает через `alloc_count` (тот же приём, что `commands::lob::
    /// session::tests::million_events_through_replay_feed_allocate_nothing_
    /// after_warmup`) на 10⁶ вызовов `on_event` в самой частой ветке живого
    /// потока — «книга ещё не готова/сигнала нет» (`Phase::Idle`, книга
    /// невалидна): она же и есть подавляющее большинство вызовов на живом
    /// потоке, где настоящий сигнал редок. Submit/cancel по настоящему
    /// сигналу вызывает учёт ордеров крейта (`hftbacktest::backtest::
    /// Backtest`), который не в этой зоне и не заявлен здесь как zero-alloc
    /// — измеряется только код этого файла, а не внутренности крейта.
    #[test]
    fn on_event_allocates_nothing_per_call_while_the_book_is_not_ready() {
        const WARMUP: usize = 2_000;
        const MEASURED: usize = 1_000_000;

        // Фид без единого обновления книги (только сделка — крейт не даёт
        // построить `Backtest` на буквально пустом фиде): `depth()` остаётся
        // `INVALID_MIN`/`INVALID_MAX` на всех вызовах, `on_event` доходит до
        // `best_prices`, видит неполную книгу и уходит в `Idle`, не трогая
        // `submit_*`/`cancel`.
        let mut hbt = seam6_backtest(&[trade_at(0, true, 100.0, 1.0)]);
        let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);

        for _ in 0..WARMUP {
            let _ = on_event(&mut hbt, &mut state);
        }

        let mut measured_allocations = 0u64;
        for _ in 0..MEASURED {
            let (action, counts) =
                crate::alloc_count::measure(|| on_event(&mut hbt, &mut state).unwrap());
            assert_eq!(action, Action::Idle, "без книги решение обязано быть Idle");
            measured_allocations += counts.allocations;
        }
        assert_eq!(
            measured_allocations, 0,
            "on_event аллоцировал на пути без книги — запрет 1 interfaces.md"
        );
    }
}
