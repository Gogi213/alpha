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
//! `best_prices` ниже — единственная реализация в `src/` (находка ревью,
//! таск 17): `lob::backtest` больше не держит одноимённый приватный хелпер
//! (снесён при переработке движка на очередь), так что копировать здесь
//! нечего — оставлена своя, потому что вызывающему (`on_event`) нужна ровно
//! эта проверка `INVALID_MIN`/`INVALID_MAX` перед тем же `entry_price`/
//! `exit_price` из `lob::backtest`.

use hftbacktest::depth::{MarketDepth, INVALID_MAX, INVALID_MIN};
use hftbacktest::types::{Bot, OrdType, Side as HbtSide, TimeInForce};

use crate::lob::backtest::{entry_price, entry_side, exit_price, ENTRY_TTL_NS, HOLD_NS};

/// Лучшие цены стороны как `(bid, ask)`, `None` — книга неполна
/// (`best_bid_tick`/`best_ask_tick` ещё на `INVALID_MIN`/`INVALID_MAX`,
/// до первого снапшота).
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
    ///
    /// Запрет 7 (книга — не хеш-отображение) добавлен таском 17 через два
    /// токена, не через голое имя типа: голое имя ловило бы легитимный
    /// `HashMapMarketDepth` — синтетическую книгу крейта `hftbacktest`,
    /// которой `mod tests` ниже кормит `Backtest` в шве 6 (`ARCHITECTURE.md`,
    /// `interfaces.md` «Швы для тестов»). Запрет 6 (цена/размер не числом с
    /// плавающей запятой) сюда сознательно не включён: `MarketDepth` крейта
    /// `hftbacktest` сама отдаёт `best_bid`/`best_ask` этим типом — это
    /// граница A9 («стратегия та же в `Backtest` и в `LiveBot`»), а не наше
    /// изобретённое число; запрет целится в собственное хранение цены/лота
    /// этого крейта, которого здесь нет.
    #[test]
    fn module_never_calls_the_wall_clock_or_uses_a_hashmap_for_the_book() {
        const SRC: &str = include_str!("strategy.rs");
        let banned = [
            concat!("Inst", "ant::now"),
            concat!("System", "Time::now"),
            concat!("Hash", "Map<"),
            concat!("Hash", "Map::"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}

#[cfg(test)]
mod tests;
