//! Запросы к состоянию: фаза, будильники, объём, номера заявок.

use super::*;

impl StrategyState {
    pub fn is_idle(&self) -> bool {
        matches!(self.phase, Phase::Idle)
    }

    /// Э-08 (T-38): позиция открыта и решена (все ноги входа — исполнены, сняты или отвергнуты) —
    /// момент форка группы форм на общем входе. `Phase` — не `pub` (граница модуля), поэтому у
    /// `lob::backtest` нет прямого доступа к варианту `Holding`.
    pub(crate) fn is_holding(&self) -> bool {
        matches!(self.phase, Phase::Holding { .. })
    }

    /// Ближайший момент, когда решение удержания может измениться **без событий рынка** (Э-04б, T-38):
    /// в фазе `Holding` плана `Bounce` без сирот `decide_exit` зависит от времени только через досрочный
    /// выход (вход + `early_exit_ns`, пока не пройден) и дедлайн (вход + `deadline_ns`); остальные входы
    /// решения — цены, стена, съедание — меняют только события. `None` — фаза или план другие (драйвер
    /// шагает как прежде). Драйвер (`run_round`) по нему пропускает пустые шаги опроса.
    pub fn hold_wakeup_ns(&self, now: i64) -> Option<i64> {
        let Phase::Holding { entry_ns } = self.phase else {
            return None;
        };
        let TradePlan::Bounce {
            deadline_ns,
            early_exit_ns,
            ..
        } = self.plan
        else {
            return None;
        };
        // TK-014 `weat*`: окно съедания и ход BTC меняются со временем без событий — шаги не пропускаются.
        if self.has_orphans() || self.wall_ring.is_some() || self.tape_on() || self.pyramid_on() {
            return None;
        }
        let deadline = entry_ns.saturating_add(deadline_ns);
        let early = entry_ns.saturating_add(early_exit_ns);
        Some(if early_exit_ns > 0 && early > now {
            early.min(deadline)
        } else {
            deadline
        })
    }

    /// Подпись входов решения удержания (TK-050): лучшие цены, объём стены, съеденное сделками и класс
    /// времени (пройдены ли пороги досрочного выхода и дедлайна). `None` — вне `Holding` плана `Bounce`,
    /// а также с сиротами и кольцом стены (там решение читает больше входов).
    pub fn hold_input_sig<MD: MarketDepth>(&self, depth: &MD, now: i64) -> Option<[u64; 5]> {
        let (
            Phase::Holding { entry_ns },
            TradePlan::Bounce {
                level_px,
                tick_px,
                deadline_ns,
                early_exit_ns,
                ..
            },
        ) = (self.phase, self.plan)
        else {
            return None;
        };
        if self.has_orphans() || self.wall_ring.is_some() || self.tape_on() || self.pyramid_on() {
            return None;
        }
        let (bid, ask) = (depth.best_bid_tick(), depth.best_ask_tick());
        #[allow(clippy::cast_possible_truncation)]
        let lt = if tick_px > 0.0 && level_px > 0.0 {
            round_half_away(level_px / tick_px) as i64
        } else {
            0
        };
        let q = if self.sigma == crate::lob::backtest::SIGMA_LONG {
            depth.bid_qty_at_tick(lt)
        } else {
            depth.ask_qty_at_tick(lt)
        };
        let early = early_exit_ns > 0 && now.saturating_sub(entry_ns) >= early_exit_ns;
        let late = now.saturating_sub(entry_ns) >= deadline_ns;
        #[allow(clippy::cast_sign_loss)]
        Some([
            bid as u64,
            ask as u64,
            q.to_bits(),
            self.eaten_qty.to_bits(),
            u64::from(early) | u64::from(late) << 1,
        ])
    }

    /// Что читает подпись `hold_input_sig` помимо лучших цен: `(лонг?, тик уровня)`; `None` там же, где у подписи
    /// (TK-048 К-4: индекс удержания прыгает по строкам, меняющим эти входы).
    pub fn hold_watch(&self) -> Option<(bool, i64)> {
        let (
            Phase::Holding { .. },
            TradePlan::Bounce {
                level_px, tick_px, ..
            },
        ) = (self.phase, self.plan)
        else {
            return None;
        };
        if self.has_orphans() || self.wall_ring.is_some() || self.tape_on() {
            return None;
        }
        #[allow(clippy::cast_possible_truncation)]
        let lt = if tick_px > 0.0 && level_px > 0.0 {
            round_half_away(level_px / tick_px) as i64
        } else {
            0
        };
        Some((self.sigma == crate::lob::backtest::SIGMA_LONG, lt))
    }

    /// Круг ждёт исполнения входа (заявка отправлена, `entry_ttl` не истёк).
    pub fn is_entry_pending(&self) -> bool {
        matches!(self.phase, Phase::EntryPending { .. })
    }

    /// Метка фазы: драйвер сравнивает её до и после `on_event` — фаза не сменилась, значит вызов
    /// был повтором решения на тех же входах.
    pub fn phase_mark(&self) -> PhaseMark {
        PhaseMark(self.phase)
    }

    /// Как `hold_wakeup_ns`, но для всех фаз круга плана `Bounce` (в том числе с живыми заявками):
    /// решение меняется без событий рынка и ответов биржи только на таймерах — срок входа
    /// (`entry_ttl`), потолок ожидания отмены (`CANCEL_WAIT_NS`), досрочный выход и дедлайн (под
    /// стоящей лимиткой выхода `decide_exit` читает те же пороги). `None` — шаги не пропускаются.
    pub fn step_wakeup_ns(&self, now: i64) -> Option<i64> {
        let TradePlan::Bounce {
            entry_ttl_ns,
            deadline_ns,
            early_exit_ns,
            ..
        } = self.plan
        else {
            return None;
        };
        if self.has_orphans() || self.wall_ring.is_some() || self.tape_on() || self.pyramid_on() {
            return None;
        }
        let held = |entry_ns: i64| {
            let deadline = entry_ns.saturating_add(deadline_ns);
            let early = entry_ns.saturating_add(early_exit_ns);
            if early_exit_ns > 0 && early > now {
                early.min(deadline)
            } else {
                deadline
            }
        };
        match self.phase {
            Phase::Idle => None,
            Phase::Holding { entry_ns } | Phase::ExitPending { entry_ns, .. } => {
                Some(held(entry_ns))
            }
            Phase::EntryPending { sent_ns, .. } => Some(sent_ns.saturating_add(entry_ttl_ns)),
            Phase::CancelPending { cancel_sent_ns, .. }
            | Phase::ExitCancelPending { cancel_sent_ns, .. } => {
                Some(cancel_sent_ns.saturating_add(CANCEL_WAIT_NS))
            }
        }
    }

    /// Размер круга: драйверу он нужен для `Fill`, сам драйвер его не хранит.
    pub fn qty(&self) -> f64 {
        self.qty
    }

    /// R2-A: id поставленных добавок — драйвер круга зачитывает их исполнения во вход.
    pub fn add_order_ids(&self) -> &[u64] {
        self.r2
            .as_deref()
            .map_or(&[][..], |r| &r.add_ids[..usize::from(self.adds_done)])
    }
}
