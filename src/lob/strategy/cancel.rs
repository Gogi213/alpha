//! Снятие заявок и типы решений: причины отмены входа, действия, котировки, выход.

use super::*;

impl StrategyState {
    /// Снимает живые ноги лестницы: исполненные и уже снятые трогать нельзя —
    /// `cancel` по ним возвращает `OrderNotFound`/`InvalidOrderStatus`, а не
    /// «ничего не произошло». Повторный вызов безопасен: нога с запросом в
    /// полёте пропускается (`cancel_open`), иначе крейт отвечает
    /// `OrderRequestInProcess` — это и позволяет звать снятие на каждом шаге
    /// фазы `CancelPending` (F8b), пока нога не стала `New`/`PartiallyFilled`.
    pub(super) fn cancel_resting<MD, B>(
        &self,
        bot: &mut B,
        first_id: u64,
        legs: u8,
    ) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        for i in 0..legs as u64 {
            self.cancel_open(bot, first_id.saturating_add(i))?;
        }
        Ok(())
    }

    /// Снять заявку, если она стоит в рынке и **не снимается прямо сейчас**:
    /// `New`/`PartiallyFilled` без запроса в полёте (`req == None`). Нога, чей
    /// запрос постановки летел в момент снятия входа, остаётся живой — её
    /// снимает следующий вызов; без него фаза ждала бы её вечно (F8b, В5/Р2).
    pub(super) fn cancel_open<MD, B>(&self, bot: &mut B, id: u64) -> Result<(), B::Error>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        // Крейт: `New`/`PartiallyFilled` без запроса в полёте (`req == None`).
        if bot
            .orders(self.asset_no)
            .get(&id)
            .is_some_and(|o| o.cancellable())
        {
            bot.cancel(self.asset_no, id, false)?;
        }
        Ok(())
    }

    /// Условия снятия неисполненного входа **до потолка** (F5, В-74):
    ///
    /// * «стена умерла» — размер на цене уровня (`level_px`) в текущем кадре
    ///   книги упал ниже порога В-66 (`level_floor_qty`, в единицах крейта);
    /// * «цена ушла из полосы» — лучшая цена нашей стороны дальше дальней
    ///   ноги лестницы больше чем на `band_exit_bps`.
    ///
    /// `None` — вход продолжает стоять: либо условий нет в плане (прежний
    /// режим `--entry-ttl-secs touch`, у него оба поля нули), либо книга ещё
    /// неполна. Размер уровня берётся **по целому тику** — тем же приёмом,
    /// что съедание E7 (`bid_qty_at_tick`/`ask_qty_at_tick`): цена уровня
    /// переводится в тик, сравнение идёт по размеру, а не по `f64`-цене.
    pub(super) fn entry_cancel_reason<MD, B>(&self, bot: &B) -> Option<EntryCancelReason>
    where
        MD: MarketDepth,
        B: Bot<MD>,
    {
        let TradePlan::Bounce {
            entry_px,
            level_px,
            tick_px,
            level_floor_qty,
            band_exit_bps,
            grid_legs,
            grid_step_px,
            ladder,
            ..
        } = self.plan
        else {
            return None;
        };
        if level_floor_qty <= 0.0 && band_exit_bps <= 0.0 {
            return None;
        }
        let entry_side = entry_side(self.sigma)?;
        let depth = bot.depth(self.asset_no);
        // Стена: размер на цене уровня в текущем кадре. Сторона уровня — та же,
        // что у входа (у лонга бид-стена, у шорта аск-стена).
        if level_floor_qty > 0.0 && tick_px > 0.0 && level_px > 0.0 {
            #[allow(clippy::cast_possible_truncation)]
            let level_tick = px_tick(level_px, tick_px);
            crate::lob::diag::set_watch_tick(level_tick);
            let now_qty = match entry_side {
                HbtSide::Buy => depth.bid_qty_at_tick(level_tick),
                _ => depth.ask_qty_at_tick(level_tick),
            };
            if now_qty < level_floor_qty {
                return Some(EntryCancelReason::WallDead);
            }
        }
        // Полоса: дальняя нога — последняя в построении входа (`px ± step × i`,
        // `Idle` ниже), расстояние до неё меряем в bps и берём по модулю
        // направления (`away`): у лонга цена уходит вверх, у шорта — вниз.
        if band_exit_bps > 0.0 && entry_px > 0.0 {
            if let Some((bid_t, ask_t)) = best_ticks(depth) {
                let away = if entry_side == HbtSide::Buy {
                    1.0
                } else {
                    -1.0
                };
                let outer = match ladder.outer_tick() {
                    // Лестница формы F6: дальняя нога — последняя в наборе.
                    #[allow(clippy::cast_precision_loss)]
                    Some(t) => t as f64 * tick_px,
                    None => entry_px + away * grid_step_px * f64::from(grid_legs.max(1) - 1),
                };
                let own_t = if entry_side == HbtSide::Buy {
                    bid_t
                } else {
                    ask_t
                };
                let own = tick_to_px(own_t, tick_px);
                if outer > 0.0 && (own - outer) * away / outer * 10_000.0 > band_exit_bps {
                    return Some(EntryCancelReason::PriceLeft);
                }
            }
        }
        None
    }
}

/// Почему неисполненный вход кончился без позиции (F5, В-74). Причина
/// считается отдельно (`forms.csv`, `n_entry_cancelled_*`): «стена снята» и
/// «цена ушла» — события рынка и повод снять вход, потолок — страховка, а
/// непоставленная биржа заявка (пост-онли, В-72) — «сигнал без входа».
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryCancelReason {
    /// Потолок `entry_ttl_ns`: вход простоял свой срок и снят (F5, В-74).
    Ttl,
    /// Стена снята: размер на `level_px` упал ниже порога В-66
    /// (`level_floor_qty`) — практики снимают по событию, не таймером.
    WallDead,
    /// Цена ушла из полосы: лучшая цена нашей стороны дальше дальней ноги
    /// лестницы больше чем на `band_exit_bps`.
    PriceLeft,
    /// Ни одной ноги биржа не поставила (пост-онли заявка пересекла спред,
    /// В-72) — «сигнал без входа», а не снятие: в счётчики снятий не входит.
    NotPlaced,
    /// Подтверждение отмены входа не пришло за `CANCEL_WAIT_NS` (F8b, В5/Р2):
    /// вход снимался, но биржа не ответила — круг освобождается, а не висит
    /// «занятым» до конца записи. Заявка могла остаться в рынке: это цена
    /// предохранителя, и она названа, а не спрятана.
    CancelTimeout,
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
    /// Вход кончился без позиции: не исполнился и снят по условию F5 (стена
    /// снята / цена ушла из полосы) или по потолку срока жизни —
    /// либо биржа не поставила ни одной ноги (пост-онли заявка пересекла
    /// спред, В-72). Круг освобождается: у такого сигнала входа не было, и
    /// «занято» он не занимает.
    EntryTimedOut {
        order_id: u64,
        /// Почему вход кончился без позиции (F5, В-74).
        reason: EntryCancelReason,
    },
    ExitSubmitted {
        order_id: u64,
        side: HbtSide,
        price: f64,
        reason: ExitReason,
        /// E7: ушла часть позиции, круг продолжается остатком.
        partial: bool,
    },
}

/// Решение о выходе из позиции: цена, тип (тейкер — по рынку, мейкер —
/// лимитом), причина и доля позиции (E7). `None` — выходить не пора или
/// книги нет (тогда круг остаётся в своей фазе к следующему событию, а не
/// теряется молча).
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct ExitDecision {
    pub(super) px: f64,
    pub(super) taker: bool,
    pub(super) reason: ExitReason,
    pub(super) frac: f64,
}

/// Где исполнить выход. `Market` — по лучшей цене встречной стороны на этом событии
/// (`exit_price`), тейкером; книги нет — выхода нет, круг ждёт следующего события.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) enum ExitAt {
    Market,
    /// Тейкером по известной цене: стоп — его уровень, трейл — цена «в пользу» на событии.
    Taker(f64),
    /// Лимитом (мейкер): тейк, горизонт Decision 20.
    Maker(f64),
}

/// Стена на цене уровня на этом событии (`StrategyState::observe_wall`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct WallNow {
    /// Уровень валиден (есть тик и цена) — без этого размера уровня нет.
    pub(super) ok: bool,
    /// Размер на цене уровня; `0.0`, если уровень не валиден.
    pub(super) qty: f64,
    /// Съедание от максимума с входа, %; `0.0`, если уровня или максимума нет.
    pub(super) eaten_pct: f64,
}

/// TK-014: причина выхода `weat*` по ходу BTC `mv` (bps) и режиму формы; `None` — режим
/// этот случай не закрывает.
pub(crate) fn wall_eat_reason_for(w: WallEatExit, mv: f64) -> Option<ExitReason> {
    let market = mv <= -w.btc_bps;
    match (w.mode, market) {
        (WallEatMode::Local, true) | (WallEatMode::Market, false) => None,
        (_, true) => Some(ExitReason::WallEatBtc),
        (_, false) => Some(ExitReason::WallEatLocal),
    }
}

/// Форма защиты по снятию стены — поля плана `exit_gone_pct`, `gone_trail_bps`, `gone_stop`
/// (в сетке `gone<W>[tr<T>|be|bex|wall<B>|wallx<B>]`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct GoneForm {
    pub(super) pct: f64,
    pub(super) trail_bps: f64,
    pub(super) stop: GoneStop,
}

/// Что решила защита по снятию на этом событии (`StrategyState::observe_gone`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct GoneGuard {
    /// `gone<W>` без продолжений: стену сняли — выход по рынку сейчас.
    pub(super) exit: bool,
    /// `gone<W>tr<T>`: откат от лучшей цены после снятия дошёл до `T`.
    pub(super) trail_hit: bool,
    /// `gone<W>bex` / `gone<W>wallx<B>`: снятие застало позицию хуже цели переноса стопа — выход
    /// по рынку сразу.
    pub(super) stop_hard_exit: bool,
    /// Стоп с учётом переноса (`be`/`bex`/`wall`/`wallx`); без него — стоп плана.
    pub(super) stop_px: f64,
}

/// Лучшие цены и сторона входа — то, от чего решается выход.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct Quotes {
    pub(super) bid_tick: i64,
    pub(super) ask_tick: i64,
    /// Шаг цены стакана: перевод тика в `f64`-цену только через него (`tick_to_px`).
    pub(super) step: f64,
    pub(super) entry_side: HbtSide,
}

impl Quotes {
    pub(super) fn bid(&self) -> f64 {
        tick_to_px(self.bid_tick, self.step)
    }
    pub(super) fn ask(&self) -> f64 {
        tick_to_px(self.ask_tick, self.step)
    }
}
