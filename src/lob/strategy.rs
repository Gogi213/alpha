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
use hftbacktest::types::{
    Bot, Event, OrdType, Side as HbtSide, Status, TimeInForce, EXCH_BUY_TRADE_EVENT,
    EXCH_SELL_TRADE_EVENT,
};

use crate::lob::backtest::{
    entry_price, entry_side, executed_notional, executed_qty, exit_price, round_half_away,
    ENTRY_TTL_NS, HOLD_NS,
};

/// G3б (TK-065): трасса решений R2 в stderr при `ALPHA_R2_TRACE=1`; только исследовательская сборка `r2`.
#[cfg(feature = "r2")]
macro_rules! r2_trace {
    ($ts:expr, $($arg:tt)*) => {{
        static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        if *ON.get_or_init(|| std::env::var_os("ALPHA_R2_TRACE").is_some()) {
            eprintln!("R2T ts={} {}", $ts, format_args!($($arg)*));
        }
    }};
}
#[cfg(not(feature = "r2"))]
macro_rules! r2_trace {
    ($ts:expr, $($arg:tt)*) => {};
}

mod cancel;
mod entry_view;
mod exit;
mod handlers;
mod idle;
mod observe;
mod orphans;
mod plan;
mod pyramid;
mod query;
mod state;
mod tape;

pub use self::cancel::*;
use self::entry_view::*;
use self::exit::*;
use self::handlers::*;
use self::idle::*;
pub use self::plan::*;
pub use self::state::*;

pub fn on_event<MD, B>(bot: &mut B, state: &mut StrategyState) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let now = bot.current_timestamp();
    // F8c (К1): сироты после потолка отмены — снять снова, учесть исполнение.
    state.sweep_orphans(bot)?;
    match state.phase {
        Phase::Holding { entry_ns } => on_holding(bot, state, now, entry_ns),
        Phase::ExitPending { order_id, entry_ns } => {
            on_exit_pending(bot, state, now, order_id, entry_ns)
        }
        Phase::ExitCancelPending {
            order_id,
            entry_ns,
            cancel_sent_ns,
        } => on_exit_cancel_pending(bot, state, now, order_id, entry_ns, cancel_sent_ns),
        Phase::EntryPending {
            order_id,
            sent_ns,
            legs,
        } => on_entry_pending(bot, state, now, order_id, sent_ns, legs),
        Phase::CancelPending {
            order_id,
            legs,
            reason,
            cancel_sent_ns,
        } => on_cancel_pending(bot, state, now, order_id, legs, reason, cancel_sent_ns),
        Phase::Idle => on_idle(bot, state, now),
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
    /// `HashMapMarketDepth` — синтетическую книгу крейта `hftbacktest` (с Э-05 тесты кормят `Backtest` её копией
    /// `FastMarketDepth` из `lob::backtest`) в шве 6 (`ARCHITECTURE.md`,
    /// `interfaces.md` «Швы для тестов»). Запрет 6 (цена/размер не числом с
    /// плавающей запятой) сюда сознательно не включён: `MarketDepth` крейта
    /// `hftbacktest` сама отдаёт `best_bid`/`best_ask` этим типом — это
    /// граница A9 («стратегия та же в `Backtest` и в `LiveBot`»), а не наше
    /// изобретённое число; запрет целится в собственное хранение цены/лота
    /// этого крейта, которого здесь нет.
    #[test]
    fn module_never_calls_the_wall_clock_or_uses_a_hashmap_for_the_book() {
        const SRC: &[&str] = &[
            include_str!("strategy.rs"),
            include_str!("strategy/entry_view.rs"),
            include_str!("strategy/plan.rs"),
            include_str!("strategy/state.rs"),
            include_str!("strategy/tape.rs"),
            include_str!("strategy/orphans.rs"),
            include_str!("strategy/observe.rs"),
            include_str!("strategy/query.rs"),
            include_str!("strategy/pyramid.rs"),
            include_str!("strategy/cancel.rs"),
            include_str!("strategy/exit.rs"),
            include_str!("strategy/handlers.rs"),
            include_str!("strategy/idle.rs"),
        ];
        let banned = [
            concat!("Inst", "ant::now"),
            concat!("System", "Time::now"),
            concat!("Hash", "Map<"),
            concat!("Hash", "Map::"),
        ];
        for b in banned {
            assert!(
                SRC.iter().all(|f| !f.contains(b)),
                "исходник тянет запрещённое: {b}"
            );
        }
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod alloc_tests;
