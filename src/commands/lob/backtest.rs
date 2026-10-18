//! Общие помощники движка для `lob bounce-grid` / `bounce-verdict` / `touches`: чтение заголовка бинлога и
//! `ReplayFeed` (`feed.rs`), форма сделки-отскока (`forms.rs`), план сделки (`plan.rs`), поля лота пула
//! (`pool.rs`). Команда `lob backtest` срезана в TK-161 (В): единственный вызывающий — `lob pilot` — ушёл в TK-168.

mod feed;
mod forms;
mod plan;
mod pool;

pub(crate) use feed::{
    count_feed_events, count_feed_events_until, open_replay_feed, read_day_schedule,
    read_tick_step, replay_compact_into_until,
};
pub(crate) use forms::PlanShape;
pub use forms::{
    BounceForm, EntryForm, EntryTtl, StopForm, TakeForm, ENTRY_TTL_SECS, MARKET_CROSS_MARGIN_BPS,
    SINGLE_ENTRY_LABEL,
};
pub(crate) use plan::{
    approach_plan, approach_plan_sched, bounce_plan, bounce_plan_sched, deadline_ns_from_secs,
    early_exit_ns_from_ms, early_label, early_ms_from_label, touch_view_of_approach,
    EARLY_EXITS_HALF_S, EARLY_EXITS_S, EARLY_EXIT_HALF_MS,
};
pub(crate) use pool::PoolLot;

#[cfg(test)]
mod tests;
