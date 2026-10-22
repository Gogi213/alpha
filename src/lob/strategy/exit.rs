//! Выход: плановый тейк, решение о выходе и подача заявки на выход.

use super::*;

/// Что решает выход: у плана Decision 20 — только горизонт; у сделки-отскока
/// (В-44) — стоп, съедание, трейл, тейк, дедлайн, и **порядок здесь часть
/// плана**: стоп приоритетнее тейка (если цена проскочила оба уровня за
/// один кадр, честнее считать, что выбило стопом), съедание всей плотности —
/// раньше трейла, трейл — раньше тейка, половинное съедание — после тейка,
/// прилипание и дедлайн — последними. Вынесено из `on_event` (аудит 21.09,
/// С2), чтобы порядок приоритетов был одной функцией и проверялся отдельно.
///
/// Г-117: цена тейка в момент `t` от входа — линейно/степенью к безубытку (вход + круг комиссий, В-63),
/// после `T` — пол; лонг округляется вверх до тика, шорт — вниз. Тейк не выше пола — без изменений.
#[inline(never)]
pub(super) fn scheduled_take(
    entry_side: HbtSide,
    entry_px: f64,
    take_px: f64,
    tick_px: f64,
    cfg: PyramidCfg,
    t_ns: i64,
    deadline_ns: i64,
) -> f64 {
    let fees = crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0;
    let long = entry_side == HbtSide::Buy;
    let floor = if long {
        entry_px * (1.0 + fees)
    } else {
        entry_px * (1.0 - fees)
    };
    let (gamma, t_frac) = if cfg.sched_g8 > 0 {
        (
            f64::from(cfg.sched_g8) / 8.0,
            f64::from(cfg.sched_t16) / 16.0,
        )
    } else {
        (
            f64::from(cfg.sched_g10) / 10.0,
            f64::from(cfg.sched_t4) / 4.0,
        )
    };
    let horizon = deadline_ns as f64 * t_frac;
    if entry_px <= 0.0
        || tick_px <= 0.0
        || horizon <= 0.0
        || (take_px - floor) * (if long { 1.0 } else { -1.0 }) <= 0.0
    {
        return take_px;
    }
    let share = (t_ns.max(0) as f64 / horizon).powf(gamma).min(1.0);
    let px = take_px - (take_px - floor) * share;
    let ticks = px / tick_px;
    (if long { ticks.ceil() } else { ticks.floor() }) * tick_px
}

/// Решение одного правила выхода: где, почему, какая доля.
type ExitHit = (ExitAt, ExitReason, f64);

/// Правила выхода сделки-отскока. Порядок здесь не важен — приоритет задаёт только `EXIT_PRIORITY`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) enum ExitRule {
    LevelHalf,
    Stop,
    GoneHard,
    EatenAll,
    Trail,
    Take,
    EatenHalf,
    EatenByTrades,
    WallEat,
    Converge,
    LevelBack,
    Tape,
    Resilience,
    GoneExit,
    GoneTrail,
    Early,
    Deadline,
}

/// Таблица приоритетов (С-13, В-44): сработавшее правило выше в списке побеждает. Стоп приоритетнее тейка
/// (проскочила оба уровня за кадр — честнее считать стопом), съедание всей плотности — раньше трейла, трейл —
/// раньше тейка, половинное съедание — после тейка, защиты `eat`/`gone` — ниже тейка, прилипание и дедлайн —
/// последние. Порядок — часть плана; тест `exit_priority` проверяет каждую пару.
pub(super) const EXIT_PRIORITY: [ExitRule; 17] = [
    ExitRule::LevelHalf,
    ExitRule::Stop,
    ExitRule::GoneHard,
    ExitRule::EatenAll,
    ExitRule::Trail,
    ExitRule::Take,
    ExitRule::EatenHalf,
    ExitRule::EatenByTrades,
    ExitRule::WallEat,
    ExitRule::Converge,
    ExitRule::LevelBack,
    ExitRule::Tape,
    ExitRule::Resilience,
    ExitRule::GoneExit,
    ExitRule::GoneTrail,
    ExitRule::Early,
    ExitRule::Deadline,
];

/// Сработавшие на этом событии правила (по одному слоту на правило, без аллокаций).
pub(super) struct ExitHits([Option<ExitHit>; EXIT_PRIORITY.len()]);

impl ExitHits {
    pub(super) const NONE: ExitHits = ExitHits([None; EXIT_PRIORITY.len()]);

    pub(super) fn set(&mut self, rule: ExitRule, hit: bool, what: ExitHit) {
        if hit {
            self.0[rule as usize] = Some(what);
        }
    }
}

/// Первое по таблице сработавшее правило.
pub(super) fn pick_exit(hits: &ExitHits) -> Option<(ExitRule, ExitHit)> {
    EXIT_PRIORITY
        .iter()
        .find_map(|&r| hits.0[r as usize].map(|h| (r, h)))
}

/// `maker_allowed = false` — лимитка выхода **уже стоит** в рынке
/// (`ExitPending`, аудит 21.09 Б1): мейкерские причины (тейк 1:1, горизонт
/// Decision 20) не рассматриваются — вторая лимитка на тот же остаток не
/// нужна, — а рыночные (стоп, съедание, трейл, прилипание, дедлайн) решают,
/// снимать ли её и добивать остаток тейкером.
///
/// Здесь — сбор сработавшего и порядок; у двух механизмов со своими защёлками
/// своё место: стена на уровне — `StrategyState::observe_wall`, защита по
/// снятию — `StrategyState::observe_gone`.
pub(super) fn decide_exit<MD, B>(
    bot: &B,
    state: &mut StrategyState,
    entry_ns: i64,
    now: i64,
    quotes: Quotes,
    maker_allowed: bool,
) -> Option<ExitDecision>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let (bid, ask, entry_side) = (quotes.bid(), quotes.ask(), quotes.entry_side);
    let (at, reason, frac) = match state.plan {
        TradePlan::SpreadHold => {
            if !maker_allowed || now.saturating_sub(entry_ns) < HOLD_NS {
                return None;
            }
            let px = exit_price(entry_side, bid, ask)?;
            (ExitAt::Maker(px), ExitReason::Horizon, 1.0)
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
            take_frac,
            eaten_half_pct,
            eaten_all_pct,
            eaten_half_frac,
            exit_eat_pct,
            exit_gone_pct,
            gone_trail_bps,
            gone_stop,
            wall_eat,
            level_floor_qty,
            ..
        } => {
            // F4 (В-78): стоп и тейк — от **средней цены исполненного**
            // входа, а не от плановой `entry_px`: лестница и частичное
            // исполнение дают другую среднюю, а форма задаёт уровни
            // расстояниями от входа. Сдвиг — целое число тиков
            // (`level_shift`), при полном исполнении по плановой цене
            // он ноль и числа прежних прогонов не меняются.
            let shift = level_shift(state.base_entry_vwap(), entry_px, tick_px);
            // Г-92: безубыток — от средней по всем ордерам (та же округлённая к тику формула).
            let be_entry_px = entry_px + level_shift(state.entry_vwap(), entry_px, tick_px);
            let (stop_px, take_px, entry_px) = (stop_px + shift, take_px + shift, entry_px + shift);
            let pyr = match state.plan {
                TradePlan::Bounce { pyramid, .. } => pyramid.eff(),
                TradePlan::SpreadHold => PyramidCfg::OFF,
            };
            let take_px = if (pyr.sched_g10 > 0 || pyr.sched_g8 > 0) && trail_bps <= 0.0 {
                scheduled_take(
                    entry_side,
                    entry_px,
                    take_px,
                    tick_px,
                    pyr,
                    now.saturating_sub(entry_ns),
                    deadline_ns,
                )
            } else {
                take_px
            };
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
            let wall = state.observe_wall(bot.depth(state.asset_no), entry_side, level_px, tick_px);
            if pyr.reinstall_n > 0 {
                state.observe_reinstall(wall, level_floor_qty);
            }
            if pyr.newwall_k > 0 {
                state.observe_newwall(
                    bot.depth(state.asset_no),
                    stop_px,
                    level_px,
                    tick_px,
                    level_floor_qty,
                    quotes,
                );
            }
            // F7 (Б-75): формы выхода «съели» / «сняли». Накопленное
            // исполнение **в стену** (`state.eaten_qty`) зачитывает драйвер
            // (`run_round::observe_wall_trades`): буфер последних сделок
            // (`bot.last_trades`) живёт под управлением драйвера и чистится на
            // каждом шаге, поэтому здесь читается только накопленная сумма.
            // Порядок: стоп и трейл честнее тейка; `eat`/`gone` — защитные
            // выходы, ниже тейка и выше «прилипания» и дедлайна.
            // TK-014 `weat*`: наибольший видимый размер стены — в корзину секунды события;
            // ход BTC считается только при пройденном пороге съедания.
            let wall_eat_reason = if wall_eat.on() {
                if wall.ok {
                    if let Some(b) = state.wall_bucket(now.div_euclid(1_000_000_000)) {
                        b.max_qty = b.max_qty.max(wall.qty);
                    }
                }
                let (eaten, max_qty) = state.wall_window(entry_ns, now);
                if max_qty > 0.0 && eaten >= max_qty * wall_eat.pct / 100.0 {
                    wall_eat
                        .btc
                        .and_then(|b| b.move_bps(now, wall_eat.secs))
                        .and_then(|mv| wall_eat_reason_for(wall_eat, mv))
                } else {
                    None
                }
            } else {
                None
            };
            let eat_hit = exit_eat_pct > 0.0
                && state.level_qty_at_entry > 0.0
                && state.eaten_qty >= state.level_qty_at_entry * exit_eat_pct / 100.0;
            let gone = state.observe_gone(
                GoneForm {
                    pct: exit_gone_pct,
                    trail_bps: gone_trail_bps,
                    stop: gone_stop,
                },
                wall,
                favourable,
                entry_side,
                entry_px,
                stop_px,
                level_px,
            );
            let mut gone = gone;
            if pyr.reinstall_n > 0 {
                let before = gone.stop_px;
                gone.stop_px =
                    state.reinstall_stop(entry_side, be_entry_px, favourable, gone.stop_px);
                #[allow(clippy::float_cmp)] // цена стопа переставлена или нет: копия, не вычисление
                if gone.stop_px != before {
                    r2_trace!(
                        bot.current_timestamp(),
                        "stop_move {} -> {} bid={} ask={}",
                        before,
                        gone.stop_px,
                        bid,
                        ask
                    );
                }
            }
            let (stop_hit, take_hit) = match entry_side {
                HbtSide::Buy => (bid <= gone.stop_px, bid >= take_px),
                _ => (ask >= gone.stop_px, ask <= take_px),
            };
            let stop_hit = if pyr.nostop_x8 > 0 {
                // Г-106: до активации стопа нет; после — безубыток (вход + круг комиссий).
                if !state.nostop_armed && entry_px > 0.0 {
                    let fees = crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0;
                    let be_px = be_entry_px * (1.0 + f64::from(state.sigma) * fees);
                    let gain_bps = f64::from(state.sigma) * (state.best_favourable - entry_px)
                        / entry_px
                        * 10_000.0;
                    state.nostop_armed = gain_bps
                        >= f64::from(pyr.nostop_x8) / 8.0 * trail_activate_bps
                        && f64::from(state.sigma) * (state.best_favourable - be_px) >= 0.0;
                }
                state.nostop_armed && {
                    let fees = crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0;
                    let be_px = be_entry_px * (1.0 + f64::from(state.sigma) * fees);
                    match entry_side {
                        HbtSide::Buy => bid <= be_px,
                        _ => ask >= be_px,
                    }
                }
            } else {
                stop_hit
            };
            // Знак сделки (R1): тот же множитель, что развёл
            // `observe_favourable`/`observe_gone_peak` по сторонам (`+1`
            // лонг, `-1` шорт) — трейл и трейл после снятия считают
            // прибыль/откат от неё им, а не модулем: `.abs()` над разностью
            // цен превращал просадку в «прибыль» — лонг, чья лучшая цена после
            // входа всё время ниже входа, взводил трейл и закрывался в минус с
            // причиной `Trail`.
            let sigma_sign = f64::from(state.sigma);
            let trail_hit = if trail_bps > 0.0 && entry_px > 0.0 {
                let gain_bps =
                    sigma_sign * (state.best_favourable - entry_px) / entry_px * 10_000.0;
                let give_back_bps =
                    sigma_sign * (state.best_favourable - favourable) / entry_px * 10_000.0;
                gain_bps >= trail_activate_bps && give_back_bps >= trail_bps
            } else {
                false
            };
            // Частичный тейк срабатывает один раз: после него остаток
            // на 1:1 не закрывается — бежит до стопа/дедлайна/трейла/
            // съедания (E7 «остаток по замедлению»).
            let take_partial = take_frac > 0.0 && take_frac < 1.0;
            let take_active = !(take_partial && state.partial_done);
            let eaten_all_hit = eaten_all_pct > 0.0 && wall.eaten_pct >= eaten_all_pct;
            let eaten_half_hit =
                eaten_half_pct > 0.0 && !state.partial_done && wall.eaten_pct >= eaten_half_pct;
            let half_stop = pyr.half_stop;
            let half_frac = if pyr.half_q8 == 0 {
                0.5
            } else {
                f64::from(pyr.half_q8) / 8.0
            };
            let converge_hit = if pyr.converge_tol1 > 0 && level_px > 0.0 && tick_px > 0.0 {
                let dmax_bps =
                    sigma_sign * (state.best_favourable - level_px) / level_px * 10_000.0;
                let back = sigma_sign * (favourable - level_px);
                dmax_bps >= f64::from(pyr.converge_a_bps)
                    && back <= f64::from(pyr.converge_tol1 - 1) * tick_px
            } else {
                false
            };
            // Г-108: возврат под уровень стены на `G` bps в окне `W` с после входа.
            let levelback_hit = pyr.levelback_secs > 0
                && level_px > 0.0
                && now.saturating_sub(entry_ns) <= i64::from(pyr.levelback_secs) * 1_000_000_000
                && sigma_sign * (level_px - favourable) / level_px * 10_000.0
                    >= f64::from(pyr.levelback_gap_bps);
            // Г-112/116: мера в окне с входа, нормированная на размер стены; только в минусе.
            let in_loss = sigma_sign * (favourable - entry_px) < 0.0;
            let tape_hit = pyr.tape_q > 0.0
                && in_loss
                && state.level_qty_at_entry > 0.0
                && state.tape_since_entry(entry_ns, now, pyr.tape_w) / state.level_qty_at_entry
                    >= pyr.tape_q;
            let cxl_hit = pyr.cxl_q > 0.0
                && in_loss
                && state.level_qty_at_entry > 0.0
                && state.cxl_since_entry(entry_ns, now, pyr.cxl_w) / state.level_qty_at_entry
                    >= pyr.cxl_q;
            #[allow(clippy::float_cmp)] // цена стопа переставлена или нет: копия, не вычисление
            let plain_stop = gone.stop_px == stop_px;
            let half_level = pyr.half_level;
            let stop_hit = stop_hit && !(half_stop && plain_stop && state.stop_half_done);
            let level_half_hit = half_level
                && !state.stop_half_done
                && (state.level_broken || (stop_hit && plain_stop));
            let stop_hit = stop_hit && !(half_level && plain_stop);
            #[allow(clippy::float_cmp)] // цена стопа переставлена или нет: копия, не вычисление
            let stop_reason = if gone.stop_px != stop_px {
                // Сработал перенесённый после снятия стоп — это защита по снятию («сняли»), а не стоп.
                ExitReason::WallGone
            } else {
                ExitReason::Stop
            };
            let stop_frac = if half_stop && plain_stop {
                half_frac
            } else {
                1.0
            };
            let take_frac_now = if take_partial { take_frac } else { 1.0 };
            let early_hit = early_exit_ns > 0
                && now.saturating_sub(entry_ns) >= early_exit_ns
                && still_at_level(quotes, level_px, tick_px);
            let mut hits = ExitHits::NONE;
            hits.set(
                ExitRule::LevelHalf,
                level_half_hit,
                (ExitAt::Market, ExitReason::Stop, half_frac),
            );
            hits.set(
                ExitRule::Stop,
                stop_hit,
                (ExitAt::Taker(gone.stop_px), stop_reason, stop_frac),
            );
            hits.set(
                ExitRule::GoneHard,
                gone.stop_hard_exit,
                (ExitAt::Market, ExitReason::WallGone, 1.0),
            );
            hits.set(
                ExitRule::EatenAll,
                eaten_all_hit,
                (ExitAt::Market, ExitReason::Eaten, 1.0),
            );
            hits.set(
                ExitRule::Trail,
                trail_hit,
                (ExitAt::Taker(favourable), ExitReason::Trail, 1.0),
            );
            hits.set(
                ExitRule::Take,
                maker_allowed && trail_bps <= 0.0 && take_hit && take_active,
                (ExitAt::Maker(take_px), ExitReason::Take, take_frac_now),
            );
            hits.set(
                ExitRule::EatenHalf,
                eaten_half_hit,
                (ExitAt::Market, ExitReason::Eaten, eaten_half_frac),
            );
            hits.set(
                ExitRule::EatenByTrades,
                eat_hit,
                (ExitAt::Market, ExitReason::EatenByTrades, 1.0),
            );
            if let Some(r) = wall_eat_reason {
                hits.set(ExitRule::WallEat, true, (ExitAt::Market, r, 1.0));
            }
            hits.set(
                ExitRule::Converge,
                converge_hit,
                (ExitAt::Market, ExitReason::Converge, 1.0),
            );
            hits.set(
                ExitRule::LevelBack,
                levelback_hit,
                (ExitAt::Market, ExitReason::LevelBack, 1.0),
            );
            hits.set(
                ExitRule::Tape,
                tape_hit,
                (ExitAt::Market, ExitReason::Tape, 1.0),
            );
            hits.set(
                ExitRule::Resilience,
                cxl_hit,
                (ExitAt::Market, ExitReason::Resilience, 1.0),
            );
            hits.set(
                ExitRule::GoneExit,
                gone.exit,
                (ExitAt::Market, ExitReason::WallGone, 1.0),
            );
            // Выход трейла после снятия — та же причина «сняли»: колонки `forms.csv`
            // не меняются, а форма (`gone<W>tr<T>`) говорит, как именно вышли.
            hits.set(
                ExitRule::GoneTrail,
                gone.trail_hit,
                (ExitAt::Taker(favourable), ExitReason::WallGone, 1.0),
            );
            // Досрочный выход (B4, В-58 п. 5): «прилипание».
            hits.set(
                ExitRule::Early,
                early_hit,
                (ExitAt::Market, ExitReason::Early, 1.0),
            );
            hits.set(
                ExitRule::Deadline,
                now.saturating_sub(entry_ns) >= deadline_ns,
                (ExitAt::Market, ExitReason::Deadline, 1.0),
            );
            let (rule, mut hit) = pick_exit(&hits)?;
            // Г-133: погоня — пока не истекло окно `W`, мейкер на лучшей цене стороны выхода (не пересекая спред);
            // стоящую лимитку тейка под погоню снимает рыночное решение (`maker_allowed == false`, погони ещё нет).
            if rule == ExitRule::Deadline
                && pyr.chase_ms > 0
                && (maker_allowed || state.chase_start_ns != 0)
            {
                if state.chase_start_ns == 0 {
                    state.chase_start_ns = now;
                }
                if now.saturating_sub(state.chase_start_ns) < i64::from(pyr.chase_ms) * 1_000_000 {
                    let px = match entry_side {
                        HbtSide::Buy => ask,
                        _ => bid,
                    };
                    hit.0 = ExitAt::Maker(px);
                }
            }
            hit
        }
    };
    let (px, taker) = match at {
        ExitAt::Market => (exit_price(entry_side, bid, ask)?, true),
        ExitAt::Taker(px) => (px, true),
        ExitAt::Maker(px) => (px, false),
    };
    Some(ExitDecision {
        px,
        taker,
        reason,
        frac,
    })
}

/// Подача заявки выхода по решению `decide_exit`: размер — своя позиция
/// круга (F4, В-78), дробный выход (E7) — вниз до кратного шага лота, стоп и
/// дедлайн — по рынку (тейкер, IOC), тейк и горизонт — лимитом (мейкер,
/// GTC). Вынесено из `on_event` (аудит 21.09): та же подача нужна и после
/// снятия зависшей лимитки тейка.
pub(super) fn submit_exit<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    entry_ns: i64,
    exit_side: HbtSide,
    decision: ExitDecision,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let ExitDecision {
        px,
        taker,
        reason,
        frac,
    } = decision;
    r2_trace!(
        bot.current_timestamp(),
        "exit reason={:?} px={} taker={} frac={} adds={}",
        reason,
        px,
        taker,
        frac,
        state.adds_done
    );
    let order_id = state.take_order_id();
    // Размер выхода — **своя позиция круга** (F4, В-78), а не плановый
    // размер: вход может исполниться частично (модель очереди по
    // объёму) или лестницей, и выход на плановый размер переворачивал
    // бы позицию, оставляя круг незакрытым. `Bot::position` крейта на
    // роль источника не годится — он частичного исполнения не видит
    // (находка F3), поэтому позиция ведётся в состоянии по ордерам.
    // Своей позиции нет (исполнение не отразилось) — прежняя
    // подстановка планового размера круга.
    // Свободная позиция: минус остаток стоящих сирот выхода (F8c, ревью 22.09
    // п. 4) — иначе исполнятся и сирота, и новый выход, и круг уйдёт в перепрод.
    let pos = {
        let pos = state.free_position();
        if pos > 0.0 {
            pos
        } else if state.position() > 0.0 {
            // Вся позиция зарезервирована сиротой выхода — ждём её (или её
            // отмены) на следующем событии, новой заявки не ставим.
            return Ok(Action::Idle);
        } else {
            state.qty
        }
    };
    // Дробный выход (E7): доля позиции, вниз до кратного шага лота;
    // ноль лотов — значит делить нечего, выходим целиком.
    let (exit_qty, partial) = if frac > 0.0 && frac < 1.0 {
        let lot = match state.plan {
            TradePlan::Bounce { lot_qty, .. } => lot_qty,
            TradePlan::SpreadHold => 0.0,
        };
        let raw = pos * frac;
        let q = if lot > 0.0 {
            (raw / lot).floor() * lot
        } else {
            raw
        };
        if q > 0.0 && q < pos {
            (q, true)
        } else {
            (pos, false)
        }
    } else {
        (pos, false)
    };
    if partial {
        state.partial_done = true;
        if reason == ExitReason::Stop {
            state.stop_half_done = true;
        }
    }
    // Стоп и дедлайн — по рынку (тейкер, IOC); тейк и горизонт —
    // лимитом (мейкер, GTC). Это не деталь реализации: издержки
    // `costs` считают тейкера и мейкера по-разному, и бэктест должен
    // видеть тот же тип ордера, что поставит живой контур.
    let (tif, ord_type) = if taker {
        (TimeInForce::IOC, OrdType::Market)
    } else {
        (TimeInForce::GTC, OrdType::Limit)
    };
    if !taker {
        crate::lob::diag::note_order_dist(
            true,
            exit_side == HbtSide::Buy,
            px,
            bot.depth(state.asset_no),
        );
    }
    match exit_side {
        HbtSide::Buy => {
            bot.submit_buy_order(state.asset_no, order_id, px, exit_qty, tif, ord_type, false)?;
        }
        _ => {
            bot.submit_sell_order(state.asset_no, order_id, px, exit_qty, tif, ord_type, false)?;
        }
    }
    if !taker && reason == ExitReason::Deadline {
        state.chase_px = px;
    }
    // Заявка выхода только ушла — исполнение зачтёт `observe_exit` на
    // следующем событии (крейт обрабатывает отклик на ближайшем
    // `elapse`), поэтому позиция закрытой ещё не считается.
    state.exit_accounted = 0.0;
    state.phase = if state.position() <= 0.0 {
        Phase::Idle
    } else {
        Phase::ExitPending { order_id, entry_ns }
    };
    Ok(Action::ExitSubmitted {
        order_id,
        side: exit_side,
        price: px,
        reason,
        partial,
    })
}
