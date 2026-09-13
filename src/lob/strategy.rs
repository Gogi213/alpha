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

/// План сделки — **данные**, а не вторая стратегия (A6): что именно ловить и
/// где выходить, решает уровень `lob/levels` (касание В-44, смерть уровня),
/// `on_event` только исполняет план. Оба варианта идут через одну и ту же
/// функцию, поэтому сделка-отскок попадает и в `Backtest`, и в `LiveBot` без
/// правок.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TradePlan {
    /// Decision 20 (как было): вход мейкером у своей стороны спреда, выход
    /// ровно на `HOLD_NS`, без стопа и тейка. Числа прежнего бэктеста этим
    /// вариантом не меняются — на нём стоит шов старого движка.
    SpreadHold,
    /// В-44, сделка-отскока: цены и срок приходят снаружи — лимитный вход по
    /// `entry_px` (за тик перед плотностью), стоп по рынку при сделке на
    /// `stop_px` (за тик внутри плотности), тейк лимитом `take_px`
    /// (R 1:1), дедлайн `deadline_ns` от момента входа — после него выход по
    /// рынку; неисполненный вход снимается через `entry_ttl_ns`.
    Bounce {
        entry_px: f64,
        stop_px: f64,
        take_px: f64,
        deadline_ns: i64,
        entry_ttl_ns: i64,
    },
}

impl TradePlan {
    /// Время жизни неисполненного входа у этого плана.
    fn entry_ttl_ns(&self) -> i64 {
        match *self {
            TradePlan::SpreadHold => ENTRY_TTL_NS,
            TradePlan::Bounce { entry_ttl_ns, .. } => entry_ttl_ns,
        }
    }
}

/// Почему отправлен выход. У Decision 20 причина одна — горизонт; у
/// сделки-отскока их три (тейк, стоп, дедлайн), и бэктест считает их
/// отдельно, потому что «сколько раз выбило стопом» — это и есть вопрос
/// практиков о винрейте.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ExitReason {
    /// Выход ровно на горизонте `HOLD_NS` (Decision 20).
    Horizon,
    /// Тейк-лимит сработал.
    Take,
    /// Стоп: сделка прошла по `stop_px`, выходим по рынку.
    Stop,
    /// Дедлайн плана истёк — выход по рынку.
    Deadline,
}

/// Состояние одного круга одной стратегии на одном активе. `sigma` — сторона
/// этого круга (`SIGMA_LONG`/`SIGMA_SHORT`, `backtest::entry_side`),
/// назначается снаружи один раз при вооружении: какой именно сигнал
/// ловить — решение уровня `lob/levels`, не этого модуля (границы модулей,
/// `interfaces.md`). `plan` — чем этот круг торгует (см. `TradePlan`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StrategyState {
    asset_no: usize,
    sigma: i8,
    qty: f64,
    next_order_id: u64,
    phase: Phase,
    plan: TradePlan,
}

impl StrategyState {
    /// Круг Decision 20: вход у спреда, выход на горизонте.
    pub fn new(asset_no: usize, sigma: i8, qty: f64, first_order_id: u64) -> Self {
        Self::with_plan(asset_no, sigma, qty, first_order_id, TradePlan::SpreadHold)
    }

    /// Круг с планом: цены и сроки сделки приходят снаружи (В-44).
    pub fn with_plan(
        asset_no: usize,
        sigma: i8,
        qty: f64,
        first_order_id: u64,
        plan: TradePlan,
    ) -> Self {
        Self {
            asset_no,
            sigma,
            qty,
            next_order_id: first_order_id,
            phase: Phase::Idle,
            plan,
        }
    }

    pub fn is_idle(&self) -> bool {
        matches!(self.phase, Phase::Idle)
    }

    /// Размер круга: драйверу он нужен для `Fill`, сам драйвер его не хранит.
    pub fn qty(&self) -> f64 {
        self.qty
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
        reason: ExitReason,
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
            // Что решает выход: у плана Decision 20 — только горизонт; у
            // сделки-отскока (В-44) — стоп, тейк и дедлайн, и порядок здесь
            // часть плана: стоп приоритетнее тейка (если цена проскочила оба
            // уровня за один кадр, честнее считать, что выбило стопом).
            let (px, taker, reason) = match state.plan {
                TradePlan::SpreadHold => {
                    if now.saturating_sub(entry_ns) < HOLD_NS {
                        return Ok(Action::Idle);
                    }
                    let Some(px) = exit_price(entry_side, bid, ask) else {
                        return Ok(Action::Idle);
                    };
                    (px, false, ExitReason::Horizon)
                }
                TradePlan::Bounce {
                    stop_px,
                    take_px,
                    deadline_ns,
                    ..
                } => {
                    let (stop_hit, take_hit) = match entry_side {
                        HbtSide::Buy => (bid <= stop_px, bid >= take_px),
                        _ => (ask >= stop_px, ask <= take_px),
                    };
                    if stop_hit {
                        (stop_px, true, ExitReason::Stop)
                    } else if take_hit {
                        (take_px, false, ExitReason::Take)
                    } else if now.saturating_sub(entry_ns) >= deadline_ns {
                        match exit_price(entry_side, bid, ask) {
                            Some(px) => (px, true, ExitReason::Deadline),
                            None => return Ok(Action::Idle),
                        }
                    } else {
                        return Ok(Action::Idle);
                    }
                }
            };
            let order_id = state.take_order_id();
            // Стоп и дедлайн — по рынку (тейкер, IOC); тейк и горизонт —
            // лимитом (мейкер, GTC). Это не деталь реализации: издержки
            // `costs` считают тейкера и мейкера по-разному, и бэктест должен
            // видеть тот же тип ордера, что поставит живой контур.
            let (tif, ord_type) = if taker {
                (TimeInForce::IOC, OrdType::Market)
            } else {
                (TimeInForce::GTC, OrdType::Limit)
            };
            match exit_side {
                HbtSide::Buy => {
                    bot.submit_buy_order(
                        state.asset_no,
                        order_id,
                        px,
                        state.qty,
                        tif,
                        ord_type,
                        false,
                    )?;
                }
                _ => {
                    bot.submit_sell_order(
                        state.asset_no,
                        order_id,
                        px,
                        state.qty,
                        tif,
                        ord_type,
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
                reason,
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
            if now.saturating_sub(sent_ns) >= state.plan.entry_ttl_ns() {
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
            // Цена входа: у Decision 20 — свой край спреда (нужна книга), у
            // сделки-отскока — цена, посчитанная уровнем заранее (В-44), и
            // книга для этого не нужна: вход стоит лимитом перед плотностью
            // и ждёт, пока цена подойдёт.
            let px = match state.plan {
                TradePlan::SpreadHold => {
                    let Some((bid, ask)) = best_prices(bot.depth(state.asset_no)) else {
                        return Ok(Action::Idle);
                    };
                    match entry_price(side, bid, ask) {
                        Some(px) => px,
                        None => return Ok(Action::Idle),
                    }
                }
                TradePlan::Bounce { entry_px, .. } => entry_px,
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
