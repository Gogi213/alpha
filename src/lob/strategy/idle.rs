//! Вход из простоя: доли лесенки и подача заявок на вход.

use super::*;

/// R3 (ревью 23.09): доли ног лестницы входа — целыми шагами лота
/// (`lot_qty`, тот же шаг, что у дробного выхода E7 — `TradePlan::lot_qty`),
/// не долями `f64` напрямую: `qty × frac`/`qty / legs` дают дробный лот
/// почти всегда, и Bybit его не примет. Правило: доля каждой ноги — вниз до
/// целого числа шагов; недостающие до целого `qty` шаги (остаток округления
/// вниз всех ног сразу) — целиком на ногу с наибольшей долей (в форме
/// `ladder<N>x<from>..<to>w<k>` это и есть утяжелённая нога — «основной объём
/// к сайзу», [T 1:31:42]; на равных долях — первая по счёту). Ноге, которой
/// не досталось ни одного шага (её доля растворилась в остатке), заявку не
/// шлём: меньше шага лота биржа отдельным ордером не примет — это и есть
/// «слить с соседней» (соседняя здесь — самая тяжёлая, не обязательно
/// следующая по индексу; свободный шаг не должен теряться, а тяжёлая нога —
/// та, что вероятнее исполнится [T 1:31:42], поэтому не расточительна).
///
/// Шаг `lot_qty` неизвестен (`<= 0.0` или не конечен — план, собранный без
/// шага записи: тесты, старое 3-польное имя формы) — деление не трогаем,
/// прежнее поведение (`qty × frac`), гейт «те же круги» не задет. У сетки
/// форм `lot_qty` — всегда шаг записи (`step_e9`), каким бы ни был источник
/// размера круга.
///
/// Сумма возвращённых долей равна `qty` (с точностью f64), пока `qty` сам —
/// целое число шагов лота: так приходит `state.qty` от `pool_order_qty*`
/// (В-89, размер круга уже `floor(...) × qty_step`), а `total_steps` тогда
/// восстанавливает то же целое, что и было. `qty`, не кратный `lot_qty` (на
/// входе этой функции не бывает), потерял бы остаток на округлении
/// `total_steps` — не эта функция это создаёт, а не то, чем её кормят.
/// Г-87: размер входа `Q0/N` (вниз до лота, не меньше лота); без `fresh` — `qty` как есть.
pub(super) fn fresh_entry_qty(plan: TradePlan, qty: f64) -> f64 {
    match plan {
        TradePlan::Bounce {
            pyramid, lot_qty, ..
        } if R2 && pyramid.fresh && pyramid.eat_parts > 0 => {
            let raw = qty / f64::from(pyramid.eat_parts);
            if lot_qty > 0.0 {
                ((raw / lot_qty).floor() * lot_qty).max(lot_qty)
            } else {
                raw
            }
        }
        _ => qty,
    }
}

pub(super) fn ladder_leg_qtys(
    qty: f64,
    lot_qty: f64,
    fracs: &[f64],
    legs: usize,
) -> [f64; MAX_ENTRY_LEGS] {
    let mut out = [0.0f64; MAX_ENTRY_LEGS];
    if legs == 0 {
        return out;
    }
    if !(lot_qty.is_finite() && lot_qty > 0.0) {
        for (i, o) in out.iter_mut().take(legs).enumerate() {
            *o = fracs.get(i).copied().unwrap_or(0.0) * qty;
        }
        return out;
    }
    #[allow(clippy::cast_possible_truncation)]
    let total_steps = round_half_away(qty / lot_qty).max(0.0) as i64;
    let mut base = [0i64; MAX_ENTRY_LEGS];
    let mut base_sum: i64 = 0;
    for (i, slot) in base.iter_mut().take(legs).enumerate() {
        let frac = fracs.get(i).copied().unwrap_or(0.0).max(0.0);
        #[allow(clippy::cast_precision_loss)]
        let ideal = frac * total_steps as f64;
        // Допуск перед округлением вниз: доля формы — отношение малых целых
        // (1/3, 2/3, …), а `f64` даёт его не точно (`2.0/3.0*3` — не ровно
        // `2.0`, а `1.9999999999999998`); без допуска `floor` теряет целый
        // шаг там, где доля должна была дать его ровно (гейт «те же круги»
        // у формы 2/3+1/3 ловил именно это). `1e-6` — на порядки больше
        // шума f64 (~1e-15 на такой сумме) и на порядки меньше практического
        // остатка формы (доли — сотые/третьи, не миллионные).
        #[allow(clippy::cast_possible_truncation)]
        let steps = if (ideal - round_half_away(ideal)).abs() < 1e-6 {
            round_half_away(ideal) as i64
        } else {
            ideal.floor() as i64
        };
        *slot = steps;
        base_sum = base_sum.saturating_add(steps);
    }
    let remainder = total_steps.saturating_sub(base_sum).max(0);
    let mut heaviest = 0usize;
    for i in 1..legs {
        if fracs.get(i).copied().unwrap_or(0.0) > fracs.get(heaviest).copied().unwrap_or(0.0) {
            heaviest = i;
        }
    }
    base[heaviest] = base[heaviest].saturating_add(remainder);
    for (o, &steps) in out.iter_mut().zip(base.iter()).take(legs) {
        #[allow(clippy::cast_precision_loss)]
        {
            *o = steps as f64 * lot_qty;
        }
    }
    out
}

/// Сигнал есть — постановка входа: одиночная лимитка, лестница `grid_*` или
/// лестница формы F6.
pub(super) fn on_idle<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    now: i64,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let Some(side) = entry_side(state.sigma) else {
        return Ok(Action::Idle);
    };
    // Цена входа: у Decision 20 — свой край спреда (нужна книга), у
    // сделки-отскока — цена, посчитанная уровнем заранее (В-44), и
    // книга для этого не нужна: вход стоит лимитом перед плотностью
    // и ждёт, пока цена подойдёт.
    let px = match state.plan {
        TradePlan::SpreadHold => {
            let depth = bot.depth(state.asset_no);
            let Some((bid, ask)) = best_ticks(depth) else {
                return Ok(Action::Idle);
            };
            // Мейкер у своей стороны спреда: лонг — лучший бид, шорт — лучший аск.
            let tick = match side {
                HbtSide::Buy => bid,
                HbtSide::Sell => ask,
                HbtSide::None | HbtSide::Unsupported => return Ok(Action::Idle),
            };
            tick_to_px(tick, depth.tick_size())
        }
        TradePlan::Bounce { entry_px, .. } => entry_px,
    };
    // Время жизни входа: у Decision 20 ордер стоит у своего спреда с
    // `GTX` (пост-онли, как было); у сделки-отскока тип задан планом:
    // по В-72 (решение владельца 20.09) вход **только мейкером** —
    // пост-онли `GTX`, и заявка, пересекшая спред, биржей
    // отклоняется (`Expired`), то есть считается не поставленной
    // (`legs_rejected`). `GTC` остался ради гейта «те же круги»:
    // прежние прогоны сняты с тейкерским входом (находка T38: `GTX` в
    // момент касания отвергается ровно там, где спред сжался до тика),
    // и воспроизводит их `--no-post-only`.
    // Ждать подтверждения запроса нужно только плану В-44: у касания
    // длительность бывает нулевой, и снять заявку по TTL раньше
    // подтверждения нельзя — крейт отвечает `OrderRequestInProcess`
    // (`backtest/proc/local.rs:222`). У Decision 20 снятие идёт через
    // 2 с, там ждать нечего.
    let (entry_tif, entry_wait) = match state.plan {
        TradePlan::SpreadHold => (TimeInForce::GTX, false),
        TradePlan::Bounce { post_only, .. } => (
            if post_only {
                TimeInForce::GTX
            } else {
                TimeInForce::GTC
            },
            true,
        ),
    };
    // Вход лестницей (решение владельца 2026-09-13): вместо одного
    // лимита — `grid_legs` штук с шагом `grid_step_px`, каждая
    // следующая дальше от плотности в сторону рынка. Размер делится
    // между ногами. С F4 (В-78) ноги **копят позицию**: исполнившаяся
    // первой остальные не снимает — лестница набирает объём, пока жив
    // вход, а средняя цена исполненного ведёт стоп и тейк.
    //
    // F6 (В-73): если у плана есть лестница формы
    // (`ladder<N>x<from>..<to>[w<k>]`), ноги берутся **из неё** — у
    // каждой свой целый тик и своя доля (нижняя нога может весить
    // вдвое: «основной объём к сайзу» [T 1:31:42]). Прежний вход
    // (`ladder.n == 0`) идёт прежним путём — на этом стоит гейт «те же
    // круги». Нога, чья цена уже перекрыла лучший аск (для покупки),
    // ставится как пост-онли (`GTX`) и получает от биржи `Expired` —
    // это и есть «не ставится» (`legs_rejected`, F4/В-72).
    let (legs, ladder, step, tick_px, lot_qty) = match state.plan {
        TradePlan::Bounce {
            grid_legs,
            grid_step_px,
            ladder,
            tick_px,
            lot_qty,
            ..
        } => {
            let legs = if ladder.n > 0 {
                ladder.n
            } else {
                grid_legs.max(1)
            };
            (legs, ladder, grid_step_px, tick_px, lot_qty)
        }
        TradePlan::SpreadHold => (1u8, EntryLadder::NONE, 0.0f64, 0.0f64, 0.0f64),
    };
    // R3: доли ног — целыми шагами лота (`ladder_leg_qtys`), не долями f64
    // напрямую. Одна нога (`legs == 1`, самый частый случай — прежний вход
    // без лестницы) несёт всю `qty` без деления вовсе (`× 1.0`/`/ 1`, точно
    // как раньше): округлять нечего, а трогать формулу значило бы дать
    // другое число там, где гейт «те же круги» сравнивает байты. Ёмкость
    // округления при двух и более ногах — `MAX_ENTRY_LEGS`, как у
    // `EntryLadder` (`push` отказывает раньше, `ladder.n` в неё всегда
    // укладывается); легаси-путь без формы F6 (`--grid-legs` без лестницы)
    // числом ног не ограничен — сверх ёмкости считаем долю как раньше
    // (`qty / legs`), не по массиву.
    let rounded_legs = legs > 1 && usize::from(legs) <= MAX_ENTRY_LEGS;
    let entry_qty = fresh_entry_qty(state.plan, state.qty);
    let leg_qtys = if rounded_legs {
        let leg_fracs: [f64; MAX_ENTRY_LEGS] = if ladder.n > 0 {
            ladder.frac
        } else {
            let mut f = [0.0f64; MAX_ENTRY_LEGS];
            let share = 1.0 / f64::from(legs);
            for slot in f.iter_mut().take(usize::from(legs)) {
                *slot = share;
            }
            f
        };
        if entry_qty < state.qty {
            // Г-87: первая часть Q0/N — одна нога (первая лестницы), остальные ноги не ставятся.
            let mut one = [0.0f64; MAX_ENTRY_LEGS];
            one[0] = entry_qty;
            one
        } else {
            ladder_leg_qtys(entry_qty, lot_qty, &leg_fracs, usize::from(legs))
        }
    } else {
        [0.0f64; MAX_ENTRY_LEGS]
    };
    let first_id = state.next_order_id;
    for i in 0..u64::from(legs) {
        let j = usize::try_from(i).unwrap_or(usize::from(legs.saturating_sub(1)));
        // Нога лестницы формы — свой тик; прежний вход — `px ± шаг × i`.
        let px_i = if ladder.n > 0 {
            if j >= usize::from(ladder.n) {
                state.take_order_id();
                continue;
            }
            tick_to_px(ladder.ticks[j], tick_px)
        } else {
            #[allow(clippy::cast_precision_loss)]
            match side {
                HbtSide::Buy => px + step * i as f64,
                _ => px - step * i as f64,
            }
        };
        let order_id = state.take_order_id();
        let qty_i = if rounded_legs {
            // R3: нога без ни одного целого шага лота (доля растворилась в
            // остатке самой тяжёлой ноги) — заявку не шлём, биржа её
            // отдельным ордером не примет (меньше шага лота). Номер заявки
            // всё равно взят (`take_order_id` выше) — счёт ног для
            // `entry_snapshot`/`legs_of`/битовой маски `run_round`
            // (`first_id .. first_id + legs`) не должен разъехаться с тем,
            // что ноги нет: они уже пропускают отсутствующий в учёте
            // крейта id, как и здесь.
            if leg_qtys[j] <= 0.0 {
                continue;
            }
            leg_qtys[j]
        } else if ladder.n > 0 {
            // Одна нога лестницы формы (`ladder.n == 1`) — как раньше,
            // `qty × frac[0]` (гейт «те же круги»).
            entry_qty * ladder.frac[j]
        } else {
            // Одна нога (`legs == 1`) или легаси `--grid-legs` сверх ёмкости
            // округления — как раньше, `qty / legs`.
            entry_qty / f64::from(legs)
        };
        crate::lob::diag::note_order_dist(
            false,
            side == HbtSide::Buy,
            px_i,
            bot.depth(state.asset_no),
        );
        match side {
            HbtSide::Buy => {
                bot.submit_buy_order(
                    state.asset_no,
                    order_id,
                    px_i,
                    qty_i,
                    entry_tif,
                    OrdType::Limit,
                    entry_wait,
                )?;
            }
            _ => {
                bot.submit_sell_order(
                    state.asset_no,
                    order_id,
                    px_i,
                    qty_i,
                    entry_tif,
                    OrdType::Limit,
                    entry_wait,
                )?;
            }
        }
    }
    state.phase = Phase::EntryPending {
        order_id: first_id,
        sent_ns: now,
        legs,
    };
    Ok(Action::EntrySubmitted {
        order_id: first_id,
        side,
        price: px,
    })
}
