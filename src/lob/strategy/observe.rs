//! Позиция, средняя цена входа, наблюдение за стеной и уходом стены.

use super::*;

impl StrategyState {
    /// Своя позиция круга: исполненный вход минус исполненные выходы (F4,
    /// В-78). Именно она — размер выхода и признак открытой позиции:
    /// `bot.position` крейта частичного исполнения не видит (находка F3),
    /// поэтому на нём решения о круге стоять не могут.
    ///
    /// R4 (ревью 23.09): `entry_qty`/`exit_qty` — суммы f64 по частичным
    /// исполнениям (ноги лестницы, добор остатка F4), и их разность сходится
    /// не в ровный ноль, а в пыль порядка 1e-17 — сравнение с нулём **точно**
    /// (как было) читало её как «позиция открыта» и держало круг в
    /// `Holding`/`ExitPending` вечно, посылая заявку выхода на пыль на каждом
    /// событии (биржа отклонила бы её по `minQty`). Единое правило — здесь, а
    /// не в каждой точке вызова (`sweep_orphans`, `on_exit_pending`,
    /// `on_entry_*`, `decide_exit`, драйвер `drive_signal`): остаток меньше
    /// половины шага лота из плана читается как отсутствие позиции — тот же
    /// допуск, что у `lot_half` в `lob::backtest` (округление дробного выхода
    /// вниз, В-78/E7).
    pub fn position(&self) -> f64 {
        let raw = (self.entry_qty - self.exit_qty).max(0.0);
        if raw > self.lot_dust_floor() {
            raw
        } else {
            0.0
        }
    }

    /// Половина шага лота плана — порог пыли для `position()`. План без шага
    /// (`SpreadHold`, либо `Bounce` со старым 3-польным именем формы, где
    /// `lot_qty` не задан/не конечен) даёт `0.0`: сравнение остаётся точным,
    /// как было (нет данных о лоте — нет допуска).
    pub(super) fn lot_dust_floor(&self) -> f64 {
        let lot = match self.plan {
            TradePlan::Bounce { lot_qty, .. } => lot_qty,
            TradePlan::SpreadHold => 0.0,
        };
        if lot.is_finite() && lot > 0.0 {
            lot * 0.5
        } else {
            0.0
        }
    }

    /// Средняя цена исполненного входа (`None` — исполнения не было).
    pub(super) fn entry_vwap(&self) -> Option<f64> {
        if self.entry_qty > 0.0 {
            Some(self.entry_notional / self.entry_qty)
        } else {
            None
        }
    }

    /// Средняя цена **базового** входа: пока добавок нет — общая; после — снимок до первой добавки
    /// (спека R2 §0: сдвиг стопа/тейка/трейла идёт по ногам базового входа, добавки его не двигают).
    pub(super) fn base_entry_vwap(&self) -> Option<f64> {
        if self.adds_done > 0 {
            if self.add_base_qty > 0.0 {
                Some(self.add_base_notional / self.add_base_qty)
            } else {
                None
            }
        } else {
            self.entry_vwap()
        }
    }

    /// Зачитать исполнение ног входа в состояние (`first_id .. first_id+legs`) —
    /// накопленный объём и стоимость исполненного. Возвращает снимок: по
    /// `open` решается, жив ли ещё вход. После входа числа заморожены — ордера
    /// остаются в учёте крейта с теми же значениями, а состояние хранит
    /// последние.
    pub(super) fn observe_entry<MD, B>(&mut self, bot: &B, first_id: u64, legs: u8) -> EntrySnapshot
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        let snap = entry_snapshot(bot, self.asset_no, first_id, legs);
        self.entry_qty = snap.qty;
        self.entry_notional = snap.notional;
        snap
    }

    /// Зачитать исполнение заявки выхода: в позицию входит **прирост** её
    /// исполненного (`exec_qty` крейта — объём последнего исполнения, у
    /// накопленного — `qty − leaves_qty`).
    pub(super) fn observe_exit<MD, B>(&mut self, bot: &B, order_id: u64)
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        let executed = bot
            .orders(self.asset_no)
            .get(&order_id)
            .map_or(0.0, executed_qty);
        if executed > self.exit_accounted {
            self.exit_qty += executed - self.exit_accounted;
            self.exit_accounted = executed;
        }
    }

    /// Позиция открыта — круг начинается: сброс частичного выхода, база
    /// съедания — размер плотности на сигнале.
    pub(super) fn enter_holding(&mut self, now: i64) {
        self.partial_done = false;
        self.eaten_qty = 0.0;
        self.adds_done = 0;
        self.adds_released = false;
        self.reinst_removed = false;
        self.reinstalls = 0;
        self.reinst_trigger = false;
        self.reinst_be_active = false;
        self.nostop_armed = false;
        self.chase_start_ns = 0;
        self.chase_px = 0.0;
        self.chase_wait_ns = -1;
        self.nw_known_n = 0;
        self.nw_init = false;
        self.stop_half_done = false;
        self.level_broken = false;
        let (level_qty, level_qty_max) = match self.plan {
            TradePlan::Bounce { level_qty, .. } => (level_qty.max(0.0), level_qty.max(0.0)),
            TradePlan::SpreadHold => (0.0, 0.0),
        };
        self.level_qty_at_entry = level_qty;
        self.level_qty_max = level_qty_max;
        self.phase = Phase::Holding { entry_ns: now };
        if self.tape_on() {
            self.tape_at_fill = self.tape_press(now);
            self.cxl_at_fill = self.cxl_press(now);
        }
    }

    /// TK-115 Г-112: лента против позиции за `W` с до исполнения входа (кольцо включено `enable_tape`), иначе 0.
    pub fn tape_at_fill(&self) -> f64 {
        self.tape_at_fill
    }

    /// TK-115 Г-133: ожидание исполнения лимиткой погони от её начала, нс; −1 — не исполнена лимиткой.
    pub fn chase_wait_ns(&self) -> i64 {
        self.chase_wait_ns
    }

    /// TK-115 Г-116: отмены нашей стороны за `W` с до исполнения входа, иначе 0.
    pub fn cxl_at_fill(&self) -> f64 {
        self.cxl_at_fill
    }

    /// F7 (Б-75): зачесть сделки этого шага, бьющие **в стену**. Вызывается
    /// драйвером (`run_round`) **до** очистки буфера `bot.last_trades` — сам
    /// буфер живёт под управлением драйвера (он чистит его на каждом шаге,
    /// чтобы память не росла с длиной круга), поэтому сделки приходят сюда
    /// снаружи, а правило «что считать ударом в стену» остаётся здесь.
    ///
    /// У бид-стены (лонг) в неё бьёт агрессор-продавец (`SELL_EVENT`), у
    /// аск-стены (шорт) — агрессор-покупатель (`BUY_EVENT`); учитывается
    /// только сделка **на нашей цене уровня** (целый тик). При выключенных
    /// формах F7 (`exit_eat_pct`/`exit_gone_pct` = 0) счётчик не читается
    /// никем — прохода по буферу нет, и числа прежних прогонов не меняются
    /// (на этом стоит гейт «те же круги»).
    pub fn observe_wall_trades(&mut self, trades: &[Event]) {
        if self.tape_on() {
            self.record_tape(trades);
        }
        let TradePlan::Bounce {
            level_px,
            tick_px,
            exit_eat_pct,
            exit_gone_pct,
            pyramid,
            ..
        } = self.plan
        else {
            return;
        };
        let pyramid = pyramid.eff();
        if self.wall_ring.is_some() {
            self.record_wall_eat(trades, level_px, tick_px);
        }
        if let (true, false, Phase::Holding { entry_ns }) =
            (pyramid.half_level, self.level_broken, self.phase)
        {
            let long = self.sigma == crate::lob::backtest::SIGMA_LONG;
            self.level_broken = trades.iter().any(|t| {
                t.exch_ts >= entry_ns && {
                    let is_trade =
                        t.ev & EXCH_SELL_TRADE_EVENT != 0 || t.ev & EXCH_BUY_TRADE_EVENT != 0;
                    is_trade
                        && if long {
                            t.px < level_px
                        } else {
                            t.px > level_px
                        }
                }
            });
        }
        // `gone<W>` тоже читает накопленное (сравнение с половиной падения),
        // поэтому счётчик ведётся при любой из двух форм.
        if exit_eat_pct <= 0.0 && exit_gone_pct <= 0.0 && pyramid.eat_parts == 0 {
            return;
        }
        if tick_px <= 0.0 || level_px <= 0.0 {
            return;
        }
        let Some(entry_side) = entry_side(self.sigma) else {
            return;
        };
        // У лонга стену едят продажи, у шорта — покупки.
        let want_sell = entry_side == HbtSide::Buy;
        #[allow(clippy::cast_possible_truncation)]
        let level_tick = px_tick(level_px, tick_px);
        let mut eaten = 0.0;
        for trade in trades {
            let hit = if want_sell {
                trade.ev & EXCH_SELL_TRADE_EVENT != 0
            } else {
                trade.ev & EXCH_BUY_TRADE_EVENT != 0
            };
            if !hit {
                continue;
            }
            #[allow(clippy::cast_possible_truncation)]
            if px_tick(trade.px, tick_px) == level_tick {
                eaten += trade.qty;
            }
        }
        self.eaten_qty += eaten;
    }

    /// TK-014 `weat*`: сделки в стену **после входа** — в корзину секунды `exch_ts`.
    pub(super) fn record_wall_eat(&mut self, trades: &[Event], level_px: f64, tick_px: f64) {
        let Phase::Holding { entry_ns } = self.phase else {
            return;
        };
        if tick_px <= 0.0 || level_px <= 0.0 {
            return;
        }
        let Some(entry_side) = entry_side(self.sigma) else {
            return;
        };
        let want_sell = entry_side == HbtSide::Buy;
        #[allow(clippy::cast_possible_truncation)]
        let level_tick = px_tick(level_px, tick_px);
        // Сторона — все биты маски: `ev & EXCH_SELL_TRADE_EVENT != 0` истинно у любой сделки
        // биржи (общие биты `TRADE_EVENT | EXCH_EVENT`) и считало бы покупки в бид-стену.
        let want = if want_sell {
            EXCH_SELL_TRADE_EVENT
        } else {
            EXCH_BUY_TRADE_EVENT
        };
        for trade in trades {
            let hit = trade.ev & want == want;
            #[allow(clippy::cast_possible_truncation)]
            let at_level = px_tick(trade.px, tick_px) == level_tick;
            if !hit || !at_level || trade.exch_ts < entry_ns {
                continue;
            }
            if let Some(b) = self.wall_bucket(trade.exch_ts.div_euclid(1_000_000_000)) {
                b.eaten += trade.qty;
            }
        }
    }

    /// Лучший исход с момента входа — для трейл-тейка: вызывается на каждом
    /// событии, пока позиция открыта. Цена «в пользу» — та, по которой
    /// закрылись бы сейчас (лучший бид для лонга, лучший аск для шорта).
    pub(super) fn observe_favourable(&mut self, price: f64) {
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

    /// Стена на цене уровня на этом событии (E5/E7, F7) — заодно ведёт максимум её размера с
    /// входа, базу съедания. Без валидного тика/цены размера уровня не существует: `qty` = 0, но
    /// **ни** «ноль на уровне», ни «стена снята» из этого не следуют (`ok`) — иначе вырождённый
    /// план с `tick_px = 0` читался бы как полностью съеденная стена.
    pub(super) fn observe_wall<MD: MarketDepth>(
        &mut self,
        depth: &MD,
        entry_side: HbtSide,
        level_px: f64,
        tick_px: f64,
    ) -> WallNow {
        if !(tick_px > 0.0 && level_px > 0.0) {
            return WallNow {
                ok: false,
                qty: 0.0,
                eaten_pct: 0.0,
            };
        }
        #[allow(clippy::cast_possible_truncation)]
        let level_tick = px_tick(level_px, tick_px);
        crate::lob::diag::set_watch_tick(level_tick);
        let qty = match entry_side {
            HbtSide::Buy => depth.bid_qty_at_tick(level_tick),
            _ => depth.ask_qty_at_tick(level_tick),
        };
        if qty > self.level_qty_max {
            self.level_qty_max = qty;
        }
        let eaten_pct = if self.level_qty_max > 0.0 {
            (1.0 - qty / self.level_qty_max) * 100.0
        } else {
            0.0
        };
        WallNow {
            ok: true,
            qty,
            eaten_pct,
        }
    }

    /// Защита по снятию стены: F7 `gone<W>` и её продолжения — трейл после снятия `tr<T>`,
    /// перенос стопа в безубыток `be`/`bex` (владелец 23.09) или на уровень стены `wall`/`wallx`
    /// (26.09). Снятие — защёлка: вернувшаяся стена ни трейл, ни перенос не выключает.
    /// `entry_px`/`stop_px` — уже сдвинутые к средней исполненного входа; `level_px` — цена стены
    /// (не сдвигается: стена стоит, где стояла).
    // Восемь аргументов — поля одного события выхода; структура ради них — лишний слой.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn observe_gone(
        &mut self,
        form: GoneForm,
        wall: WallNow,
        favourable: f64,
        entry_side: HbtSide,
        entry_px: f64,
        stop_px: f64,
        level_px: f64,
    ) -> GoneGuard {
        // «Сняли» (F7, Б-75): стена упала ниже (1 − W %) от размера на входе, и сделками съедено
        // меньше половины падения. Размер уровня читается только при валидном тике: иначе «ноль
        // на уровне» — это отсутствие данных, а не снятая стена.
        let hit = form.pct > 0.0
            && self.level_qty_at_entry > 0.0
            && wall.ok
            && wall.qty < self.level_qty_at_entry * (1.0 - form.pct / 100.0)
            && self.eaten_qty < (self.level_qty_at_entry - wall.qty) * 0.5;
        // Трейл после снятия: снятие взводит трейл ценой этого события (дальше пик ведётся и без
        // стены), выход — откат от пика после снятия. Откат знаковый (R1, см. `decide_exit`).
        let trail = form.trail_bps > 0.0;
        if trail && (hit || self.gone_peak > 0.0) {
            self.observe_gone_peak(favourable);
        }
        let trail_hit = trail
            && self.gone_peak > 0.0
            && entry_px > 0.0
            && f64::from(self.sigma) * (self.gone_peak - favourable) / entry_px * 10_000.0
                >= form.trail_bps;
        // Перенос стопа после снятия: цель — безубыток (круг закрывается в ноль с комиссиями: вход
        // мейкером, выход тейкером) или уровень стены с буфером за ним; стоп переезжает, как только
        // позиция у цели или лучше. Жёсткий режим закрывает позицию хуже цели сразу; режим `wallk`
        // в этом случае отменяет перенос до конца сделки.
        let mut keep = false;
        let (hard, target) = match form.stop {
            GoneStop::Off => (false, None),
            GoneStop::Breakeven { hard } => {
                let fees = crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0;
                let be_px = match entry_side {
                    HbtSide::Buy => entry_px * (1.0 + fees),
                    _ => entry_px * (1.0 - fees),
                };
                (hard, (entry_px > 0.0).then_some(be_px))
            }
            GoneStop::Wall { mode, buffer_bps } => {
                let buffer = buffer_bps / 10_000.0;
                let wall_px = match entry_side {
                    HbtSide::Buy => level_px * (1.0 - buffer),
                    _ => level_px * (1.0 + buffer),
                };
                keep = mode == WallStopMode::Keep;
                (
                    mode == WallStopMode::Hard,
                    (level_px > 0.0).then_some(wall_px),
                )
            }
        };
        let first_hit = hit && !self.gone_seen;
        if form.stop != GoneStop::Off && hit {
            self.gone_seen = true;
        }
        if let Some(target) = target {
            let at_target = match entry_side {
                HbtSide::Buy => favourable >= target,
                _ => favourable <= target,
            };
            if keep && first_hit && !at_target {
                self.gone_stop_off = true;
            }
            if self.gone_seen && at_target && !self.gone_stop_off {
                self.gone_stop_active = true;
            }
        }
        let stop_px = match target {
            Some(target) if self.gone_stop_active => match entry_side {
                HbtSide::Buy => stop_px.max(target),
                _ => stop_px.min(target),
            },
            _ => stop_px,
        };
        GoneGuard {
            exit: hit && !trail && form.stop == GoneStop::Off,
            trail_hit,
            stop_hard_exit: hard && self.gone_seen && !self.gone_stop_active,
            stop_px,
        }
    }

    /// Лучший исход с момента снятия стены — для трейла после снятия: первый вызов взводит
    /// трейл ценой снятия, дальше вызывается на каждом событии, пока позиция открыта.
    pub(super) fn observe_gone_peak(&mut self, price: f64) {
        if price <= 0.0 {
            return;
        }
        let better = if self.sigma == crate::lob::backtest::SIGMA_LONG {
            self.gone_peak == 0.0 || price > self.gone_peak
        } else {
            self.gone_peak == 0.0 || price < self.gone_peak
        };
        if better {
            self.gone_peak = price;
        }
    }
}
