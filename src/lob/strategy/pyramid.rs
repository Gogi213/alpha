//! Пирамида: добор долями, переустановка у новой стены.

use super::*;

impl StrategyState {
    /// R2-A: доливка включена — решение удержания читает добавки и сделки в стену, шаги не пропускаются.
    pub(super) fn pyramid_on(&self) -> bool {
        matches!(self.plan, TradePlan::Bounce { pyramid, .. } if pyramid.on())
    }

    pub(super) fn take_order_id(&mut self) -> u64 {
        let id = self.next_order_id;
        self.next_order_id = self.next_order_id.saturating_add(1);
        id
    }

    /// R2-A: зачесть исполнение добавок в позицию плана (`entry_*` = база + добавки).
    #[inline(never)]
    pub(super) fn pyramid_account<MD, B>(&mut self, bot: &B)
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        if self.adds_done == 0 {
            return;
        }
        let mut qty = self.add_base_qty;
        let mut notional = self.add_base_notional;
        for id in self.add_order_ids() {
            let Some(order) = bot.orders(self.asset_no).get(id) else {
                continue;
            };
            let executed = executed_qty(order);
            if executed > 0.0 {
                qty += executed;
                notional += executed_notional(order);
            }
        }
        self.entry_qty = qty;
        self.entry_notional = notional;
    }

    /// R2-A, Г-94: на съедании `j/N` стены (`j = 1..N−1`) — добавка `Q0/N` пост-онли лимитом по
    /// лучшей цене нашей стороны; стена съедена целиком — добавки нет; не больше одной за вызов.
    #[inline(never)]
    pub(super) fn pyramid_step<MD, B>(
        &mut self,
        bot: &mut B,
        bid: f64,
        ask: f64,
        side: HbtSide,
    ) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        let TradePlan::Bounce {
            pyramid,
            lot_qty,
            post_only,
            ..
        } = self.plan
        else {
            return Ok(());
        };
        let parts = pyramid.eat_parts;
        if parts == 0 || self.adds_released || self.exit_qty > 0.0 || self.partial_done {
            return Ok(());
        }
        let j = self.adds_done + 1;
        if j >= parts || usize::from(self.adds_done) >= MAX_ADDS || self.level_qty_at_entry <= 0.0 {
            return Ok(());
        }
        let wall = self.level_qty_at_entry;
        if self.eaten_qty < wall * f64::from(j) / f64::from(parts) || self.eaten_qty >= wall {
            return Ok(());
        }
        if self.adds_done == 0 {
            self.add_base_qty = self.entry_qty;
            self.add_base_notional = self.entry_notional;
        }
        r2_trace!(
            bot.current_timestamp(),
            "pyramid_add n={} eaten={} wall={} bid={} ask={}",
            self.adds_done + 1,
            self.eaten_qty,
            wall,
            bid,
            ask
        );
        let id = self.take_order_id();
        self.r2.get_or_insert_with(R2Bufs::boxed).add_ids[usize::from(self.adds_done)] = id;
        self.adds_done += 1;
        let raw = if pyramid.fresh {
            self.qty / f64::from(parts)
        } else {
            self.add_base_qty / f64::from(parts)
        };
        let qty = if lot_qty > 0.0 {
            (raw / lot_qty).floor() * lot_qty
        } else {
            raw
        };
        if qty <= 0.0 {
            return Ok(());
        }
        let tif = if post_only {
            TimeInForce::GTX
        } else {
            TimeInForce::GTC
        };
        match side {
            HbtSide::Buy => {
                bot.submit_buy_order(self.asset_no, id, bid, qty, tif, OrdType::Limit, true)?;
            }
            _ => {
                bot.submit_sell_order(self.asset_no, id, ask, qty, tif, OrdType::Limit, true)?;
            }
        }
        Ok(())
    }

    /// Г-92: ведёт снятие/возврат стены на цене уровня (порог — `level_floor_qty` базы). Возврат —
    /// событие: счёт `N` и добавка; кадры не различаются (решение по состоянию книги на событии).
    #[inline(never)]
    pub(super) fn observe_reinstall(&mut self, wall: WallNow, floor: f64) {
        let TradePlan::Bounce { pyramid, .. } = self.plan else {
            return;
        };
        if pyramid.reinstall_n == 0 || !wall.ok || floor <= 0.0 || self.adds_released {
            return;
        }
        if !self.reinst_removed {
            if wall.qty < floor {
                self.reinst_removed = true;
            }
        } else if wall.qty >= floor {
            self.reinst_removed = false;
            self.reinstalls = self.reinstalls.saturating_add(1);
            self.reinst_trigger = true;
        }
    }

    /// Г-93: убыточная позиция и на нашей стороне между стопом и лучшей ценой (строго) появился уровень
    /// размером ≥ `floor`, которого не было к первому событию удержания, — триггер добавки. Первое
    /// событие только снимает снимок известных стен. Каждая стена даёт триггер один раз.
    #[inline(never)]
    #[allow(clippy::too_many_arguments)]
    pub(super) fn observe_newwall<MD: MarketDepth>(
        &mut self,
        depth: &MD,
        side: HbtSide,
        stop_px: f64,
        level_px: f64,
        tick_px: f64,
        floor: f64,
        bid: f64,
        ask: f64,
    ) {
        let TradePlan::Bounce { pyramid, .. } = self.plan else {
            return;
        };
        if pyramid.newwall_k == 0
            || floor <= 0.0
            || tick_px <= 0.0
            || self.adds_released
            || self.exit_qty > 0.0
        {
            return;
        }
        #[allow(clippy::cast_possible_truncation)]
        let (stop_t, level_t) = (
            round_half_away(stop_px / tick_px) as i64,
            round_half_away(level_px / tick_px) as i64,
        );
        let buy = matches!(side, HbtSide::Buy);
        let (from, step) = if buy {
            (depth.best_bid_tick() - 1, -1_i64)
        } else {
            (depth.best_ask_tick() + 1, 1_i64)
        };
        let init = !self.nw_init;
        let losing = self
            .entry_vwap()
            .is_some_and(|v| if buy { bid < v } else { ask > v });
        if !init && !losing {
            return;
        }
        let mut t = from;
        let mut n = 0;
        while n < NW_SCAN_MAX && (t - stop_t) * step < 0 {
            n += 1;
            let tick = t;
            t += step;
            if tick == level_t {
                continue;
            }
            let qty = if buy {
                depth.bid_qty_at_tick(tick)
            } else {
                depth.ask_qty_at_tick(tick)
            };
            let known = &mut self.r2.get_or_insert_with(R2Bufs::boxed).nw_known;
            if qty < floor || known[..usize::from(self.nw_known_n)].contains(&tick) {
                continue;
            }
            if usize::from(self.nw_known_n) < NW_KNOWN {
                known[usize::from(self.nw_known_n)] = tick;
                self.nw_known_n += 1;
            }
            if !init {
                self.reinst_trigger = true;
            }
        }
        self.nw_init = true;
    }

    /// Г-92: безубыток после `N`-го возврата (мягкий: переезд, когда цена у безубытка или лучше).
    #[inline(never)]
    pub(super) fn reinstall_stop(
        &mut self,
        entry_side: HbtSide,
        entry_px: f64,
        favourable: f64,
        stop_px: f64,
    ) -> f64 {
        let TradePlan::Bounce { pyramid, .. } = self.plan else {
            return stop_px;
        };
        if pyramid.reinstall_n == 0 || self.reinstalls < pyramid.reinstall_n || entry_px <= 0.0 {
            return stop_px;
        }
        let fees = crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0;
        let (be_px, at_target) = match entry_side {
            HbtSide::Buy => {
                let be = entry_px * (1.0 + fees);
                (be, favourable >= be)
            }
            _ => {
                let be = entry_px * (1.0 - fees);
                (be, favourable <= be)
            }
        };
        if at_target {
            self.reinst_be_active = true;
        }
        if !self.reinst_be_active {
            return stop_px;
        }
        match entry_side {
            HbtSide::Buy => stop_px.max(be_px),
            _ => stop_px.min(be_px),
        }
    }

    /// Г-92: возврат стены — одна добавка `u·Q0` по лучшей цене нашей стороны; висящая прошлая
    /// добавка занимает слот (новая не ставится, `adds_done` не растёт); не больше `K`.
    #[inline(never)]
    pub(super) fn pyramid_reinstall_step<MD, B>(
        &mut self,
        bot: &mut B,
        bid: f64,
        ask: f64,
        side: HbtSide,
    ) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        if !self.reinst_trigger {
            return Ok(());
        }
        self.reinst_trigger = false;
        let TradePlan::Bounce {
            pyramid,
            lot_qty,
            post_only,
            ..
        } = self.plan
        else {
            return Ok(());
        };
        let (cap, u3) = if pyramid.newwall_k > 0 {
            (pyramid.newwall_k, pyramid.newwall_u3)
        } else {
            (REINSTALL_K, pyramid.reinstall_u3)
        };
        if (pyramid.reinstall_n == 0 && pyramid.newwall_k == 0)
            || self.adds_released
            || self.exit_qty > 0.0
            || self.partial_done
            || self.adds_done >= cap
            || usize::from(self.adds_done) >= MAX_ADDS
        {
            return Ok(());
        }
        let busy = self.adds_done > 0
            && bot
                .orders(self.asset_no)
                .get(&self.add_order_ids()[usize::from(self.adds_done) - 1])
                .is_some_and(|o| {
                    matches!(
                        o.status,
                        Status::None | Status::New | Status::PartiallyFilled
                    )
                });
        if busy {
            return Ok(());
        }
        if self.adds_done == 0 {
            self.add_base_qty = self.entry_qty;
            self.add_base_notional = self.entry_notional;
        }
        let raw = self.add_base_qty * f64::from(u3) / 3.0;
        let qty = if lot_qty > 0.0 {
            (raw / lot_qty).floor() * lot_qty
        } else {
            raw
        };
        if qty <= 0.0 {
            return Ok(());
        }
        r2_trace!(
            bot.current_timestamp(),
            "reinstall_add n={} qty={} bid={} ask={}",
            self.adds_done + 1,
            qty,
            bid,
            ask
        );
        let id = self.take_order_id();
        self.r2.get_or_insert_with(R2Bufs::boxed).add_ids[usize::from(self.adds_done)] = id;
        self.adds_done += 1;
        let tif = if post_only {
            TimeInForce::GTX
        } else {
            TimeInForce::GTC
        };
        match side {
            HbtSide::Buy => {
                bot.submit_buy_order(self.asset_no, id, bid, qty, tif, OrdType::Limit, true)?;
            }
            _ => {
                bot.submit_sell_order(self.asset_no, id, ask, qty, tif, OrdType::Limit, true)?;
            }
        }
        Ok(())
    }

    /// R2-A: выход отправлен — добавки снимаются, поздние исполнения гасятся как сироты входа.
    #[inline(never)]
    pub(super) fn pyramid_release<MD, B>(&mut self, bot: &mut B) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        if self.adds_released || self.adds_done == 0 {
            return Ok(());
        }
        self.adds_released = true;
        for i in 0..usize::from(self.adds_done) {
            let id = self.add_order_ids()[i];
            self.cancel_open(bot, id)?;
            let executed = bot.orders(self.asset_no).get(&id).map_or(0.0, executed_qty);
            self.adopt_orphans(id, 1, OrphanKind::Entry, executed);
        }
        Ok(())
    }
}
