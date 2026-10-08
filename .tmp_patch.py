p = 'src/lob/backtest.rs'
s = open(p, encoding='utf-8').read()


def rep(a, b):
    global s
    assert s.count(a) >= 1, a
    s = s.replace(a, b, 1)


rep("""    skip_cap: Option<i64>,
) -> Result<(RoundOutcome, f64), B::Error>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut timed_out = false;""", """    skip_cap: Option<i64>,
) -> Result<(RoundOutcome, f64), B::Error>
where
    B: Bot<MD> + 'static,
    MD: MarketDepth,
{
    let mut timed_out = false;""")
rep("""    let mut solo_sig = SigMemo::default();
    loop {
        // Э-04б: в удержании без заявок пустые шаги опроса пропускаются""", """    let mut solo_sig = SigMemo::default();
    // TK-049 (`ALPHA_FAST_HOLD`): один заход быстрого пути на круг; записи ног входа сохраняются перед заменой движка.
    let mut fast_tried = !fast_hold::fast_hold_on();
    let mut post_step = false;
    let mut saved: Vec<(u64, Order)> = Vec::new();
    loop {
        if !fast_tried
            && entry_pending == 0
            && exits.is_empty()
            && skip_on_hold(skip_cap, ev_steps)
            && state.is_holding()
            && state.hold_wakeup_ns(bot.current_timestamp()).is_some()
            && !has_open_orders(bot, asset_no)
        {
            fast_tried = true;
            let legs_saved: Vec<(u64, Order)> = (0..u64::from(legs.max(1)))
                .filter_map(|i| {
                    let id = entry_id.saturating_add(i);
                    bot.orders(asset_no).get(&id).map(|o| (id, o.clone()))
                })
                .collect();
            if let Some(cap) = skip_cap {
                if let fast_hold::FastOutcome::Swapped(r) = fast_hold::try_fast_hold(
                    bot,
                    asset_no,
                    state,
                    cap,
                    decided_in_hold,
                    stable,
                    solo_sig,
                    skip_on,
                ) {
                    *state = r.state;
                    decided_in_hold = r.decided_in_hold;
                    stable = r.stable;
                    solo_sig = r.sig;
                    post_step = r.post_step;
                    saved = legs_saved;
                }
            }
        }
        let skip_step = std::mem::take(&mut post_step);
        // Э-04б: в удержании без заявок пустые шаги опроса пропускаются""")
rep("""        let stepped = match (wakeup, skip_cap) {
            (Some(th), Some(cap)) => {
                STEP_KINDS[2].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let resp = !state.is_holding() || has_open_orders(bot, asset_no);
                hold_step(bot, th, cap, resp)?
            }""", """        let stepped = match (wakeup, skip_cap) {
            _ if skip_step => ElapseResult::Ok,
            (Some(th), Some(cap)) => {
                STEP_KINDS[2].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let resp = !state.is_holding() || has_open_orders(bot, asset_no);
                hold_step(bot, th, cap, resp)?
            }""")
rep("""        if entry_pending == 0 {
            state.observe_wall_trades(bot.last_trades(asset_no));
            bot.clear_last_trades(Some(asset_no));
        }
        // Решение удержания принято на этой точке, только если""", """        if entry_pending == 0 && !skip_step {
            state.observe_wall_trades(bot.last_trades(asset_no));
            bot.clear_last_trades(Some(asset_no));
        }
        // Решение удержания принято на этой точке, только если""")
rep("""        let entry_status = bot.orders(asset_no).get(&entry_id).map(|o| o.status);
        return Ok((
            RoundOutcome::TimedOut {
                entry_status,
                legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),""", """        let entry_status = order_of(bot, asset_no, &saved, entry_id).map(|o| o.status);
        return Ok((
            RoundOutcome::TimedOut {
                entry_status,
                legs_rejected: rejected_legs_with(bot, asset_no, entry_id, legs, &saved),""")
rep("""        let Some(o) = bot.orders(asset_no).get(&entry_id.saturating_add(i)) else {
            continue;
        };
        // Заказанное""", """        let Some(o) = order_of(bot, asset_no, &saved, entry_id.saturating_add(i)) else {
            continue;
        };
        // Заказанное""")
rep("""        if let Some(o) = bot
            .orders(asset_no)
            .get(exit_id)
            .filter(|o| executed_qty(o) > 0.0)
        {
            let exec = executed_qty(o);
            exit_qty += exec;""", """        if let Some(o) =
            order_of(bot, asset_no, &saved, *exit_id).filter(|o| executed_qty(o) > 0.0)
        {
            let exec = executed_qty(o);
            exit_qty += exec;""")
rep("""                        legs_rejected: rejected_legs(bot, asset_no, entry_id, legs),
                        fill_by_cross,
                    },
                    exit_ts,""", """                        legs_rejected: rejected_legs_with(bot, asset_no, entry_id, legs, &saved),
                        fill_by_cross,
                    },
                    exit_ts,""")
rep("""fn rejected_legs<B, MD>(bot: &B, asset_no: usize, entry_id: u64, legs: u8) -> u8
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut n = 0u8;
    for i in 0..u64::from(legs.max(1)) {
        if bot
            .orders(asset_no)
            .get(&entry_id.saturating_add(i))
            .is_some_and(|o| matches!(o.status, Status::Rejected | Status::Expired))""", """fn rejected_legs<B, MD>(bot: &B, asset_no: usize, entry_id: u64, legs: u8) -> u8
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    rejected_legs_with(bot, asset_no, entry_id, legs, &[])
}

/// Заявка по номеру: у движка, а если движок заменён быстрым путём удержания — из копии, снятой до замены.
fn order_of<'a, B, MD>(
    bot: &'a B,
    asset_no: usize,
    saved: &'a [(u64, Order)],
    id: u64,
) -> Option<&'a Order>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    bot.orders(asset_no)
        .get(&id)
        .or_else(|| saved.iter().find(|(i, _)| *i == id).map(|(_, o)| o))
}

fn rejected_legs_with<B, MD>(
    bot: &B,
    asset_no: usize,
    entry_id: u64,
    legs: u8,
    saved: &[(u64, Order)],
) -> u8
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let mut n = 0u8;
    for i in 0..u64::from(legs.max(1)) {
        if order_of(bot, asset_no, saved, entry_id.saturating_add(i))
            .is_some_and(|o| matches!(o.status, Status::Rejected | Status::Expired))""")
rep("""/// Есть ли у круга заявки, ещё живые в крейте""", """/// Условие быстрого пути (TK-049): пропуск пустых шагов включён, событийные шаги выключены.
fn skip_on_hold(skip_cap: Option<i64>, ev_steps: bool) -> bool {
    skip_cap.is_some() && !ev_steps
}

/// Есть ли у круга заявки, ещё живые в крейте""")
rep("""                    let s = if bt.elapse(0)? == ElapseResult::EndOfData {
                        SignalStep::EndOfData
                    } else {
                        step(bt, data_end)?
                    };""", """                    let s = if bt.elapse(0)? == ElapseResult::EndOfData {
                        SignalStep::EndOfData
                    } else {
                        fast_hold::with_fast_ctx(
                            rest,
                            windows.tick_size,
                            windows.lot_size,
                            exec_latency,
                            cfg.queue_model,
                            || step(bt, data_end),
                        )?
                    };""")
open(p, 'w', encoding='utf-8').write(s)
