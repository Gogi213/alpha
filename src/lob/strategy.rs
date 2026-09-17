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
use hftbacktest::types::{Bot, OrdType, Side as HbtSide, Status, TimeInForce};

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

/// Стоит ли лучшая цена стороны **на уровне касания** (B4, В-58 п. 5):
/// «касание не кончилось» — цена не ушла ни в отскок, ни сквозь уровень.
/// Сравнение с точностью половины тика: цены собираются умножением целых тиков
/// на шаг, и равенство `f64` здесь было бы проверкой арифметики, а не рынка.
/// Уровень берётся со стороны входа: у лонга (бид-уровень) это лучший бид, у
/// шорта — лучший аск. Выключенный или неполный план (`tick_px = 0`) не даёт
/// «прилипания»: считать его на неизвестном шаге — выдумывать признак.
fn still_at_level(entry_side: HbtSide, bid: f64, ask: f64, level_px: f64, tick_px: f64) -> bool {
    if tick_px <= 0.0 || level_px <= 0.0 {
        return false;
    }
    let side_px = match entry_side {
        HbtSide::Buy => bid,
        _ => ask,
    };
    (side_px - level_px).abs() < tick_px * 0.5
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
    /// Вход отправлен, ждём исполнения или истечения `entry_ttl_ns`. Лестница
    /// ставит несколько ордеров подряд: `order_id` — первый, всего `legs`.
    EntryPending {
        order_id: u64,
        sent_ns: i64,
        legs: u8,
    },
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
        /// Досрочный выход «по прилипанию» (B4, В-58 п. 5): секунды `X` из
        /// предрегистрированного набора {1, 2, 3} в наносекундах, `0` —
        /// выключен. Если через `X` после входа лучшая цена стороны всё ещё
        /// стоит на уровне входа касания (`level_px` с точностью до половины
        /// `tick_px`), касание не разрешилось — выходим по рынку с причиной
        /// `Early`.
        early_exit_ns: i64,
        /// Цена уровня касания `P` — по ней решается «уровень ещё держит»
        /// (B4). Не путать с `entry_px`: вход может стоять от фронтранера.
        level_px: f64,
        /// Шаг цены инструмента: сравнение «цена ещё на уровне» — с точностью
        /// половины тика, а не равенством `f64` (цены приходят из целых тиков,
        /// но собираются умножением на шаг).
        tick_px: f64,
        /// Вход пост-онли (`GTX`) или обычным лимитом (`GTC`). Параметр, а не
        /// константа: на старте касания цена уже на плотности, и пересекает
        /// ли вход спред — вопрос замера (T38), не догадки.
        post_only: bool,
        /// Трейл-тейк: откат от лучшей цены «в пользу позиции», bps, при
        /// котором выходим по рынку. `0` — трейл выключен, работает
        /// фиксированный `take_px` (решение владельца 2026-09-13: тянуть
        /// прибыль дальше 1:1).
        trail_bps: f64,
        /// Прибыль от входа, после которой трейл включается, bps. До неё
        /// работает только стоп — иначе трейл выбивал бы на первом шуме.
        trail_activate_bps: f64,
        /// Вход лестницей (решение владельца 2026-09-13): сколько лимитов
        /// ставить вместо одного. `1` — прежнее поведение.
        grid_legs: u8,
        /// Шаг лестницы в цене (не в тиках: тик знает вызывающий, у стратегии
        /// его нет). Первая нога — `entry_px`, каждая следующая дальше от
        /// плотности, в сторону рынка.
        grid_step_px: f64,
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
    /// Трейл-тейк: цена откатилась от лучшего исхода на `trail_bps`, выходим
    /// по рынку (решение владельца 2026-09-13 — тянуть прибыль дальше 1:1).
    Trail,
    /// Досрочный выход по «прилипанию» (B4, В-58 п. 5): через `early_exit_ns`
    /// после входа уровень **всё ещё лучшая цена** — касание не разрешилось ни
    /// в отскок, ни в пробой, и сделка стоит в нём, платя за неопределённость.
    /// Выход по рынку (тейкер). Причина считается отдельно от `deadline`:
    /// дедлайн — конец плана, «прилипание» — свойство касания.
    Early,
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
    /// Лучшая цена «в пользу позиции» с момента входа (для трейл-тейка): у
    /// лонга — максимум лучшего бида, у шорта — минимум лучшего аска.
    /// `0.0` — вход ещё не состоялся.
    best_favourable: f64,
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
            best_favourable: 0.0,
        }
    }

    /// Лучший исход с момента входа — для трейл-тейка: вызывается на каждом
    /// событии, пока позиция открыта. Цена «в пользу» — та, по которой
    /// закрылись бы сейчас (лучший бид для лонга, лучший аск для шорта).
    fn observe_favourable(&mut self, price: f64) {
        if price <= 0.0 {
            return;
        }
        // Сравнение в `if`, а не образцом по константе: `SIGMA_LONG` в позиции
        // образца стал бы новым связыванием, а не сравнением (clippy:
        // unreachable pattern, «переменная должна быть snake_case»).
        let better = if self.sigma == crate::lob::backtest::SIGMA_LONG {
            self.best_favourable == 0.0 || price > self.best_favourable
        } else {
            self.best_favourable == 0.0 || price < self.best_favourable
        };
        if better {
            self.best_favourable = price;
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

    /// Снимает живые ноги лестницы: исполненные и уже снятые трогать нельзя —
    /// `cancel` по ним возвращает `OrderNotFound`/`InvalidOrderStatus`, а не
    /// «ничего не произошло».
    fn cancel_resting<MD, B>(&self, bot: &mut B, first_id: u64, legs: u8) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        for i in 0..legs as u64 {
            let id = first_id.saturating_add(i);
            let status = bot.orders(self.asset_no).get(&id).map(|o| o.status);
            if matches!(status, Some(Status::New) | Some(Status::PartiallyFilled)) {
                bot.cancel(self.asset_no, id, false)?;
            }
        }
        Ok(())
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
                    entry_px,
                    stop_px,
                    take_px,
                    deadline_ns,
                    trail_bps,
                    trail_activate_bps,
                    early_exit_ns,
                    level_px,
                    tick_px,
                    ..
                } => {
                    // Трейл-тейк (решение владельца 2026-09-13): следим за
                    // лучшим исходом и выходим по рынку, когда цена откатилась
                    // от него на `trail_bps`, но не раньше, чем прибыль дошла
                    // до `trail_activate_bps`. Пока трейл включён, фиксированный
                    // `take_px` не работает — иначе он и был бы выходом, а мы
                    // как раз пробуем тянуть дальше 1:1.
                    let favourable = match entry_side {
                        HbtSide::Buy => bid,
                        _ => ask,
                    };
                    state.observe_favourable(favourable);
                    let (stop_hit, take_hit) = match entry_side {
                        HbtSide::Buy => (bid <= stop_px, bid >= take_px),
                        _ => (ask >= stop_px, ask <= take_px),
                    };
                    let trail_hit = if trail_bps > 0.0 && entry_px > 0.0 {
                        let gain_bps =
                            (state.best_favourable - entry_px).abs() / entry_px * 10_000.0;
                        let give_back_bps =
                            (state.best_favourable - favourable).abs() / entry_px * 10_000.0;
                        gain_bps >= trail_activate_bps && give_back_bps >= trail_bps
                    } else {
                        false
                    };
                    if stop_hit {
                        (stop_px, true, ExitReason::Stop)
                    } else if trail_hit {
                        (favourable, true, ExitReason::Trail)
                    } else if trail_bps <= 0.0 && take_hit {
                        (take_px, false, ExitReason::Take)
                    } else if early_exit_ns > 0
                        && now.saturating_sub(entry_ns) >= early_exit_ns
                        && still_at_level(entry_side, bid, ask, level_px, tick_px)
                    {
                        // Досрочный выход (B4, В-58 п. 5): «прилипание» —
                        // касание длится дольше `X` секунд, а уровень так и
                        // остался лучшей ценой. Порядок проверок часть плана:
                        // стоп и трейл (если сработали) честнее, тейк-лимит
                        // тоже — он дал бы мейкерскую цену, а здесь выход по
                        // рынку.
                        match exit_price(entry_side, bid, ask) {
                            Some(px) => (px, true, ExitReason::Early),
                            None => return Ok(Action::Idle),
                        }
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
            // Размер выхода — **фактическая** позиция, а не плановый размер
            // круга: лестница входа может набрать часть ног (агрессор выедает
            // несколько уровней подряд), и выход на плановый размер
            // переворачивал бы позицию, оставляя круг незакрытым навсегда —
            // это и был дефект, который поймал тест лестницы (T38).
            let exit_qty = {
                let pos = bot.position(state.asset_no).abs();
                if pos > 0.0 {
                    pos
                } else {
                    state.qty
                }
            };
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
                        exit_qty,
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
                        exit_qty,
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
        Phase::EntryPending {
            order_id,
            sent_ns,
            legs,
        } => {
            if bot.position(state.asset_no) != 0.0 {
                // Первая нога исполнилась — остальные снимаем: добор
                // усреднением это уже другая сделка, и решать её отдельно
                // (лестница здесь только выбирает цену входа).
                state.cancel_resting(bot, order_id, legs)?;
                state.phase = Phase::Holding { entry_ns: now };
                return Ok(Action::Idle);
            }
            if now.saturating_sub(sent_ns) >= state.plan.entry_ttl_ns() {
                state.cancel_resting(bot, order_id, legs)?;
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
            // Время жизни входа: у Decision 20 ордер стоит у своего спреда с
            // `GTX` (пост-онли, как было); у сделки-отскока цена задана
            // снаружи (за тик перед плотностью), и `GTX` там отвергается
            // биржей ровно в момент касания, когда спред сжался до тика —
            // поэтому вход обычным лимитом `GTC`. Это находка прогона T38, а
            // Тип входного ордера — параметр плана, а не константа: пост-онли
            // вход в момент касания может пересекать спред (вопрос «сколько
            // таких касаний» решает замер, а не догадка), а `GTC` входит
            // тейкером там, где пересекает. У Decision 20 тип прежний — `GTX`.
            // Ждать подтверждения запроса нужно только плану В-44: у касания
            // длительность бывает нулевой, и снять заявку по TTL раньше
            // подтверждения нельзя — крейт отвечает `OrderRequestInProcess`
            // (`backtest/proc/local.rs:222`). У Decision 20 снятие идёт через
            // 2 с, там ждать нечего.
            let (entry_tif, entry_wait) = match state.plan {
                TradePlan::SpreadHold => (TimeInForce::GTX, false),
                TradePlan::Bounce { post_only, .. } => (
                    if post_only {
                        TimeInForce::GTX
                    } else {
                        TimeInForce::GTC
                    },
                    true,
                ),
            };
            // Вход лестницей (решение владельца 2026-09-13): вместо одного
            // лимита — `grid_legs` штук с шагом `grid_step_px`, каждая
            // следующая дальше от плотности в сторону рынка. Размер делится
            // между ногами; исполняется, как правило, одна (первую же и
            // держим — остальные снимаются при заполнении), поэтому лестница
            // выбирает цену входа, а не усредняет позицию.
            let (legs, step) = match state.plan {
                TradePlan::Bounce {
                    grid_legs,
                    grid_step_px,
                    ..
                } => (grid_legs.max(1), grid_step_px),
                TradePlan::SpreadHold => (1u8, 0.0f64),
            };
            let leg_qty = state.qty / legs as f64;
            let first_id = state.next_order_id;
            for i in 0..legs as u64 {
                let px_i = match side {
                    HbtSide::Buy => px + step * i as f64,
                    _ => px - step * i as f64,
                };
                let order_id = state.take_order_id();
                match side {
                    HbtSide::Buy => {
                        bot.submit_buy_order(
                            state.asset_no,
                            order_id,
                            px_i,
                            leg_qty,
                            entry_tif,
                            OrdType::Limit,
                            entry_wait,
                        )?;
                    }
                    _ => {
                        bot.submit_sell_order(
                            state.asset_no,
                            order_id,
                            px_i,
                            leg_qty,
                            entry_tif,
                            OrdType::Limit,
                            entry_wait,
                        )?;
                    }
                }
            }
            state.phase = Phase::EntryPending {
                order_id: first_id,
                sent_ns: now,
                legs,
            };
            Ok(Action::EntrySubmitted {
                order_id: first_id,
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
