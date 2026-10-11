//! Сироты заявок и счётчики отмен, приём плана и номера заявок.

use super::*;

impl StrategyState {
    /// F8b (В5/Р2): сколько раз круг пережил потолок ожидания подтверждения
    /// отмены лимитки выхода — читает драйвер для колонки `forms.csv`.
    pub fn exit_cancel_timeouts(&self) -> u64 {
        self.exit_cancel_timeouts
    }

    /// F8c (К1): сколько раз исполнилась заявка-сирота (после потолка
    /// отмены) — колонка `n_orphan_fills`; ноль — норма.
    pub fn orphan_fills(&self) -> u64 {
        self.orphan_fills
    }

    /// F8c (К1): сколько раз новая партия сирот вытеснила живую прежнюю.
    pub fn orphan_overflow(&self) -> u64 {
        self.orphan_overflow
    }

    /// Сироты ещё стоят в рынке (или их снятие летит).
    pub fn has_orphans(&self) -> bool {
        !self.orphans.is_empty()
    }

    /// Забрать партии сирот у состояния (драйвер — на границе круга): у
    /// состояния их больше нет, у следующего состояния будут (`inherit_orphans`).
    pub fn take_orphans(&mut self) -> OrphanCarry {
        let c = self.orphans;
        self.orphans = OrphanCarry::NONE;
        self.orphan_exit_open = 0.0;
        c
    }

    /// Унаследовать партии сирот прежнего круга (F8c): уборка продолжится с
    /// первого события нового круга. Своих партий у нового состояния ещё нет.
    pub fn inherit_orphans(&mut self, carry: OrphanCarry) {
        self.orphans = carry;
    }

    /// Э-08 (T-38, «один проход на вход»): вариант группы форм расходится с
    /// остальными только выходом — после общего входа (форк на `Holding`)
    /// клону ставится план **этого** варианта (стоп/тейк/дедлайн/трейл/…);
    /// поля входа к этому моменту `on_holding`/`decide_exit` уже не читают
    /// (`on_entry_pending` — единственный читатель входных полей плана,
    /// а он отработал до форка), так что подмена безопасна.
    /// Г-65: `set_plan` для варианта с `wall2` — вторая живая стена противоположной стороны за входом на `fill_ms`
    /// по журналу `walls`; цель = её цена ∓/± 1 тик (`wall2`: 1 — перед стеной, 2 — за ней), фиксированный
    /// `take_px` при `trail_bps = 0`. Меньше двух стен — план без изменений (сделка = B1).
    pub(crate) fn set_plan_wall2(
        &mut self,
        mut plan: TradePlan,
        walls: &[crate::lob::levels::WallEvent],
        fill_ms: i64,
        long: bool,
        live: &mut Vec<i64>,
    ) {
        use crate::book::Side;
        use crate::lob::levels::second_wall_tick;
        if let TradePlan::Bounce {
            entry_px,
            ref mut take_px,
            ref mut trail_bps,
            tick_px,
            pyramid,
            ..
        } = plan
        {
            let w2 = pyramid.eff().wall2;
            if w2 > 0 && tick_px > 0.0 {
                let shift = level_shift(self.base_entry_vwap(), entry_px, tick_px);
                let entry_tick = px_tick(entry_px + shift, tick_px);
                let wall_side = if long { Side::Ask } else { Side::Bid };
                if let Some(t) = second_wall_tick(walls, wall_side, fill_ms, entry_tick, long, live)
                {
                    // «перед стеной» у лонга — ниже, у шорта — выше; «за» — наоборот.
                    let before = if long { -1 } else { 1 };
                    let off = if w2 == 1 { before } else { -before };
                    *take_px = (t + off) as f64 * tick_px - shift;
                    *trail_bps = 0.0;
                }
            }
        }
        self.set_plan(plan);
    }

    pub(crate) fn set_plan(&mut self, plan: TradePlan) {
        self.plan = plan;
        if matches!(plan, TradePlan::Bounce { pyramid, .. } if pyramid.on()) && self.r2.is_none() {
            self.r2 = Some(R2Bufs::boxed());
        }
        // TK-014: клон общего входа группы получает кольцо `weat*` своего плана (раз на круг).
        if let TradePlan::Bounce { wall_eat, .. } = plan {
            if wall_eat.on()
                && self.wall_ring.as_ref().map(|r| r.len()) != Some(wall_eat.secs as usize)
            {
                self.wall_ring =
                    Some(vec![WallBucket::EMPTY; wall_eat.secs as usize].into_boxed_slice());
            }
        }
    }

    /// Э-08: свой диапазон номеров заявок на клон (иначе K вариантов одного
    /// круга ставили бы заявки выхода с одинаковыми `order_id` в общий
    /// движок — крейт требует уникальности). Конкретное значение наружу не
    /// уходит (в `rounds.csv`/`forms.csv` номеров нет, находка ревью Э-08) —
    /// подходит любое неперекрывающееся с остальными клонами и с ногами
    /// общего входа.
    pub(crate) fn set_next_order_id(&mut self, id: u64) {
        self.next_order_id = id;
    }

    /// Потолок отмены сорвал ожидание: ноги `first .. first + legs` — новая
    /// партия сирот (F8c, К1). Ёмкость исчерпана — вытесняется старейшая,
    /// переполнение считается.
    ///
    /// `accounted` — исполненное этих ног, **уже** учтённое планом к моменту
    /// усыновления (B1, ревью 23.09): у ног входа — `entry_qty` (позиция,
    /// которую план дальше ведёт со стопом и выходом), у заявки выхода —
    /// `exit_accounted` (уже в `exit_qty`). Прежде партия заводилась с нулём, и
    /// первая же уборка считала это исполнение заново: вход гасился по рынку
    /// поверх позиции плана (план потом выходил ещё раз — позиция на бирже
    /// уходила в минус), выход зачитывался дважды (план считал позицию
    /// закрытой, а она оставалась — без стопа). Сирота отвечает только за
    /// исполнение **после** усыновления.
    pub(super) fn adopt_orphans(&mut self, first: u64, legs: u8, kind: OrphanKind, accounted: f64) {
        let b = OrphanBatch {
            first,
            legs: legs.max(1),
            kind,
            accounted: accounted.max(0.0),
        };
        let n = self.orphans.len();
        if n < MAX_ORPHAN_BATCHES {
            self.orphans.batches[n] = b;
            self.orphans.n += 1;
        } else {
            self.orphan_overflow = self.orphan_overflow.saturating_add(1);
            self.orphans.batches.copy_within(1.., 0);
            self.orphans.batches[MAX_ORPHAN_BATCHES - 1] = b;
        }
    }

    /// Свободная позиция под новый выход: своя позиция минус остаток стоящих
    /// сирот выхода (они ещё могут исполниться).
    pub(super) fn free_position(&self) -> f64 {
        (self.position() - self.orphan_exit_open).max(0.0)
    }

    /// F8c (К1): на каждом событии, в любой фазе — сироты снимаются снова,
    /// пока стоят, а их исполнение учитывается: нога входа гасится по рынку
    /// (IOC по лучшей цене другой стороны), нога выхода зачитывается в
    /// `exit_qty`. Партия отпускается, когда ни одна нога не стоит и не
    /// снимается. Без сирот — ноль работы.
    pub(super) fn sweep_orphans<MD, B>(&mut self, bot: &mut B) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        if self.orphans.is_empty() {
            return Ok(());
        }
        let mut exit_open = 0.0_f64;
        let mut k = 0usize;
        while k < self.orphans.len() {
            let mut b = self.orphans.batches[k];
            let mut alive = false;
            let mut executed = 0.0_f64;
            let mut side = HbtSide::None;
            for i in 0..u64::from(b.legs) {
                let id = b.first.saturating_add(i);
                // Снимок полей заявки — до `bot.cancel` (заём карты крейта).
                let Some((exec, o_side, cancellable, in_flight, leaves)) =
                    bot.orders(self.asset_no).get(&id).map(|o| {
                        (
                            executed_qty(o),
                            o.side,
                            o.cancellable(),
                            o.req != Status::None || o.status == Status::None,
                            o.leaves_qty.max(0.0),
                        )
                    })
                else {
                    continue;
                };
                executed += exec;
                side = o_side;
                if cancellable {
                    bot.cancel(self.asset_no, id, false)?;
                }
                if cancellable || in_flight {
                    alive = true;
                    if b.kind == OrphanKind::Exit {
                        exit_open += leaves;
                    }
                }
            }
            let delta = executed - b.accounted;
            if delta > 0.0 {
                match b.kind {
                    OrphanKind::Exit => {
                        self.exit_qty += delta;
                        b.accounted = executed;
                        self.orphan_fills = self.orphan_fills.saturating_add(1);
                        // Сирота закрыла позицию целиком, пока план её вёл:
                        // выхода ставить не на что (`Holding` нулевой позиции
                        // отправил бы заявку на ноль).
                        if self.position() <= 0.0 && matches!(self.phase, Phase::Holding { .. }) {
                            self.phase = Phase::Idle;
                        }
                    }
                    OrphanKind::Entry => {
                        let depth = bot.depth(self.asset_no);
                        let ticks = best_ticks(depth);
                        let tick_size = depth.tick_size();
                        let flat_side = match side {
                            HbtSide::Buy => HbtSide::Sell,
                            HbtSide::Sell => HbtSide::Buy,
                            HbtSide::None | HbtSide::Unsupported => HbtSide::None,
                        };
                        // Цена гашения — как у выхода из позиции той же стороны
                        // (`exit_price` берёт сторону **позиции**: лонг гасится по
                        // биду). Нет книги или стороны — гасить нечем, повтор на
                        // следующем событии (`accounted` не двигается).
                        #[allow(clippy::cast_precision_loss)]
                        let px_of = |t: i64| t as f64 * tick_size;
                        if let Some(px) = ticks.and_then(|(b, a)| match side {
                            HbtSide::Buy => Some(px_of(b)),
                            HbtSide::Sell => Some(px_of(a)),
                            HbtSide::None | HbtSide::Unsupported => None,
                        }) {
                            let id = self.take_order_id();
                            match flat_side {
                                HbtSide::Buy => bot.submit_buy_order(
                                    self.asset_no,
                                    id,
                                    px,
                                    delta,
                                    TimeInForce::IOC,
                                    OrdType::Market,
                                    false,
                                )?,
                                _ => bot.submit_sell_order(
                                    self.asset_no,
                                    id,
                                    px,
                                    delta,
                                    TimeInForce::IOC,
                                    OrdType::Market,
                                    false,
                                )?,
                            };
                            b.accounted = executed;
                            self.orphan_fills = self.orphan_fills.saturating_add(1);
                        }
                    }
                }
            }
            if alive {
                self.orphans.batches[k] = b;
                k += 1;
            } else {
                // Партия отпущена: сдвиг хвоста, ёмкость фиксированная.
                let n = self.orphans.len();
                self.orphans.batches.copy_within(k + 1..n, k);
                self.orphans.n -= 1;
            }
        }
        self.orphan_exit_open = exit_open;
        Ok(())
    }
}
