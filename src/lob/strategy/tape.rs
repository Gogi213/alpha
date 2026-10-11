//! Лента и отмены у стены: окна, давление, срезы с момента входа.

use super::*;

impl StrategyState {
    /// TK-115 Г-112: журнал ленты включён. Без feature `r2` константно `false` — ветки не доходят до боевого бинарника.
    #[inline(always)]
    pub(super) fn tape_on(&self) -> bool {
        R2 && self.tape_ring.is_some()
    }

    /// TK-132 С-03: окно журнала ленты приходит из конфига круга (`DriveConfig::tape_log_secs`); 0 — выключено,
    /// без feature `r2` — тоже ничего (кольца не создаются).
    pub fn enable_tape_from(&mut self, secs: u32) {
        if R2 && secs > 0 {
            self.enable_tape(secs);
        }
    }

    /// TK-115 Г-112: включить кольцо ленты на `secs` секунд (журнал `[fill−W, fill]`); без вызова ничего не меняется.
    pub fn enable_tape(&mut self, secs: u32) {
        self.cxl_ring = Some(
            vec![
                TapeBucket {
                    sec: i64::MIN,
                    lots: 0.0
                };
                secs.max(1) as usize
            ]
            .into_boxed_slice(),
        );
        self.tape_ring = Some(
            vec![
                TapeBucket {
                    sec: i64::MIN,
                    lots: 0.0
                };
                secs.max(1) as usize
            ]
            .into_boxed_slice(),
        );
    }

    /// TK-115 Г-112: сделки этого шага, бьющие **против** позиции (лонг — продажи тейкера), в корзины секунд.
    pub(super) fn record_tape(&mut self, trades: &[Event]) {
        let Some(entry_side) = entry_side(self.sigma) else {
            return;
        };
        let want = if entry_side == HbtSide::Buy {
            EXCH_SELL_TRADE_EVENT
        } else {
            EXCH_BUY_TRADE_EVENT
        };
        let Some(ring) = self.tape_ring.as_mut() else {
            return;
        };
        for t in trades.iter().filter(|t| t.ev & want == want) {
            let sec = t.exch_ts.div_euclid(1_000_000_000);
            #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
            let i = sec.rem_euclid(ring.len() as i64) as usize;
            let b = &mut ring[i];
            if b.sec != sec {
                *b = TapeBucket { sec, lots: 0.0 };
            }
            b.lots += t.qty;
        }
    }

    /// TK-115 Г-116: сумма отмен за последние `W` секунд до `now` включительно. Без кольца — 0.
    pub fn cxl_press(&self, now: i64) -> f64 {
        let Some(ring) = self.cxl_ring.as_ref() else {
            return 0.0;
        };
        let t = now.div_euclid(1_000_000_000);
        #[allow(clippy::cast_possible_wrap)]
        let lo = t - ring.len() as i64 + 1;
        ring.iter()
            .filter(|b| b.sec >= lo && b.sec <= t)
            .map(|b| b.lots)
            .sum()
    }

    /// TK-115 Г-116: отмены на нашей стороне между `stop_px` и лучшей ценой: убыль размера уровня между вызовами
    /// минус сделки против позиции на этой цене. Зовёт драйвер перед `observe_wall_trades` с теми же сделками шага
    /// (вызов пропускается, пока сделки отложены, — снимок тогда стареет вместе с буфером). До исполнения входа.
    pub fn observe_cancels<MD: MarketDepth>(&mut self, depth: &MD, trades: &[Event], now: i64) {
        if !R2 || self.cxl_ring.is_none() {
            return;
        }
        let keep_in_hold =
            matches!(self.plan, TradePlan::Bounce { pyramid, .. } if pyramid.cxl_q > 0.0);
        if matches!(
            self.phase,
            Phase::ExitPending { .. } | Phase::ExitCancelPending { .. }
        ) || (matches!(self.phase, Phase::Holding { .. }) && !keep_in_hold)
        {
            return;
        }
        let TradePlan::Bounce {
            stop_px, tick_px, ..
        } = self.plan
        else {
            return;
        };
        let Some(side) = entry_side(self.sigma) else {
            return;
        };
        if tick_px <= 0.0 {
            return;
        }
        let buy = side == HbtSide::Buy;
        let best = if buy {
            depth.best_bid_tick()
        } else {
            depth.best_ask_tick()
        };
        if best == INVALID_MIN || best == INVALID_MAX {
            return;
        }
        #[allow(clippy::cast_possible_truncation)]
        let stop_t = px_tick(stop_px, tick_px);
        let idx_of = |tick: i64| if buy { tick - stop_t } else { stop_t - tick };
        let want = if buy {
            EXCH_SELL_TRADE_EVENT
        } else {
            EXCH_BUY_TRADE_EVENT
        };
        let book = self.cxl_book.get_or_insert_with(|| {
            Box::new(CxlBook {
                snap: [0.0; NW_SCAN_MAX as usize],
                traded: [0.0; NW_SCAN_MAX as usize],
                init: false,
                last_hi: 0,
            })
        });
        for t in trades.iter().filter(|t| t.ev & want == want) {
            #[allow(clippy::cast_possible_truncation)]
            let i = idx_of(px_tick(t.px, tick_px));
            if (0..NW_SCAN_MAX).contains(&i) {
                #[allow(clippy::cast_sign_loss)]
                {
                    book.traded[i as usize] += t.qty;
                }
            }
        }
        let hi = idx_of(best).max(book.last_hi).min(NW_SCAN_MAX - 1);
        let mut cancelled = 0.0;
        for i in 0..=hi {
            let tick = if buy { stop_t + i } else { stop_t - i };
            let q = if buy {
                depth.bid_qty_at_tick(tick)
            } else {
                depth.ask_qty_at_tick(tick)
            };
            #[allow(clippy::cast_sign_loss)]
            let k = i as usize;
            if book.init {
                let dec = book.snap[k] - q - book.traded[k];
                if dec > 0.0 {
                    cancelled += dec;
                }
            }
            book.snap[k] = q;
            book.traded[k] = 0.0;
        }
        book.init = true;
        book.last_hi = idx_of(best).clamp(0, NW_SCAN_MAX - 1);
        if cancelled > 0.0 {
            if let Some(ring) = self.cxl_ring.as_mut() {
                let sec = now.div_euclid(1_000_000_000);
                #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
                let i = sec.rem_euclid(ring.len() as i64) as usize;
                let b = &mut ring[i];
                if b.sec != sec {
                    *b = TapeBucket { sec, lots: 0.0 };
                }
                b.lots += cancelled;
            }
        }
    }

    /// TK-115 Г-112: лоты против позиции за последние `W` секунд до `now` включительно. Без кольца — 0.
    pub fn tape_press(&self, now: i64) -> f64 {
        let Some(ring) = self.tape_ring.as_ref() else {
            return 0.0;
        };
        let t = now.div_euclid(1_000_000_000);
        #[allow(clippy::cast_possible_wrap)]
        let lo = t - ring.len() as i64 + 1;
        ring.iter()
            .filter(|b| b.sec >= lo && b.sec <= t)
            .map(|b| b.lots)
            .sum()
    }

    /// TK-115 Г-112/116: сумма корзин кольца за последние `W` с до `now`, но только секунды строго после секунды входа
    /// (корзина секунды входа смешана с до-входом — отбрасываем: занижает меру, выход консервативнее, spec §1b).
    pub(super) fn ring_since_entry(
        ring: Option<&[TapeBucket]>,
        entry_ns: i64,
        now: i64,
        w: u32,
    ) -> f64 {
        let Some(ring) = ring else {
            return 0.0;
        };
        let t = now.div_euclid(1_000_000_000);
        #[allow(clippy::cast_possible_wrap)]
        let lo = (t - i64::from(w).min(ring.len() as i64) + 1)
            .max(entry_ns.div_euclid(1_000_000_000) + 1);
        ring.iter()
            .filter(|b| b.sec >= lo && b.sec <= t)
            .map(|b| b.lots)
            .sum()
    }

    /// Г-112: лента против позиции в окне с входа (`entry_ns`), лоты. Без кольца — 0.
    pub fn tape_since_entry(&self, entry_ns: i64, now: i64, w: u32) -> f64 {
        Self::ring_since_entry(self.tape_ring.as_deref(), entry_ns, now, w)
    }

    /// Г-116: отмены на нашей стороне в окне с входа, лоты. Без кольца — 0.
    pub fn cxl_since_entry(&self, entry_ns: i64, now: i64, w: u32) -> f64 {
        Self::ring_since_entry(self.cxl_ring.as_deref(), entry_ns, now, w)
    }

    /// Корзина секунды `sec` кольца `weat*` (обнуляется, если в ней лежала другая секунда).
    pub(super) fn wall_bucket(&mut self, sec: i64) -> Option<&mut WallBucket> {
        let ring = self.wall_ring.as_mut()?;
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let i = sec.rem_euclid(ring.len() as i64) as usize;
        let b = &mut ring[i];
        if b.sec != sec {
            *b = WallBucket {
                sec,
                eaten: 0.0,
                max_qty: 0.0,
            };
        }
        Some(b)
    }

    /// TK-014: по окну `[max(вход, t − W), t]` (секунды) — исполнено по цене стены и наибольший
    /// видимый размер стены. Без кольца — `(0, 0)`.
    pub(super) fn wall_window(&self, entry_ns: i64, now: i64) -> (f64, f64) {
        let Some(ring) = self.wall_ring.as_ref() else {
            return (0.0, 0.0);
        };
        let t = now.div_euclid(1_000_000_000);
        #[allow(clippy::cast_possible_wrap)]
        let lo = (t - ring.len() as i64 + 1).max(entry_ns.div_euclid(1_000_000_000));
        let (mut eaten, mut max_qty) = (0.0, 0.0_f64);
        for b in ring.iter() {
            if b.sec >= lo && b.sec <= t {
                eaten += b.eaten;
                max_qty = max_qty.max(b.max_qty);
            }
        }
        (eaten, max_qty)
    }
}
