//! План сделки-отскока (таск 38, В-44 → В-62 → В-65 → F6/F7), общий с `lob
//! bounce-grid`: порог «стена снята» (`notional_floor_qty`, F5), сама сборка
//! плана из касания (`bounce_plan`) или записи подхода (`touch_view_of_
//! approach`/`approach_plan`, F6), профили касаний по осям (`BounceRow`/
//! `bounce_rows`) и предрегистрированные сетки дедлайна/раннего выхода
//! (`DEADLINES_S`/`EARLY_EXITS_S`, `deadline_ns_from_secs`/
//! `early_exit_ns_from_secs`). Вынесено из `backtest` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use crate::book::Side;
use crate::lob::backtest::{SIGMA_LONG, SIGMA_SHORT};
use crate::lob::levels::{
    ApproachRecord, TouchRecord, REACTION_WINDOWS_S, STRENGTH_HELD_WINDOWS_S,
};
use crate::lob::step_schedule::StepSchedule;
use crate::lob::strategy::{EntryLadder, TradePlan};
use crate::lob::touch_axes::{
    age_bucket, frontrun_bucket, frontrun_share, round_bucket, touch_index_bucket,
};

use crate::commands::lob::profiles::size_bucket;
use crate::commands::lob::side_name;

use super::forms::{
    bps_to_ticks_ceil, ladder_legs, ladder_legs_distinct, BounceForm, EntryForm, EntryTtl,
    PlanShape, StopForm, TakeForm, MARKET_CROSS_MARGIN_BPS,
};

/// Порог В-66 в единицах размера крейта для условия «стена снята» (F5,
/// В-74): `--h3-usd / цена уровня`. Размер уровня в книге крейта —
/// `size_at_touch × шаг лота` (`level_qty` плана), а номинал уровня —
/// `цена × размер`, поэтому порог в тех же единицах — `usd / цена`. Число
/// приходит флагом (`--h3-usd`); нет цены или номинала — порога нет (`0`),
/// и вызывающий обязан отказать раньше, если условия F5 включены.
fn notional_floor_qty(h3_usd: Option<f64>, price: f64) -> f64 {
    match h3_usd {
        Some(usd) if usd.is_finite() && usd > 0.0 && price.is_finite() && price > 0.0 => {
            usd / price
        }
        _ => 0.0,
    }
}

/// План сделки-отскока для касания по форме базы (В-44 → В-62 → В-65):
/// бид-уровень `P` — покупка лимитом от первого фронтранера (иначе `P + 1`
/// тик), стоп по рынку по форме `form.stop`, тейк лимитом по `form.take`;
/// аск зеркально. Расстояния в bps — в целых тиках вверх
/// (`bps_to_ticks_ceil`). Форма входа (F6, В-73) — `shape.entry_form`:
/// `single@fr` — прежняя одиночная нога, лестница — `N` ног от `from` до `to`
/// bps над стеной целыми тиками, где совпавшие тики сложены, а опорная цена
/// стопа/тейка — средняя по долям (фактическую среднюю ведёт стратегия после
/// F4). Срок жизни входа — `shape.entry_ttl` (F5, В-74):
/// `touch` — конец касания (гейт), секунды — потолок, а вход снимается
/// раньше по «стена снята» (`level_floor_qty`) или «цена ушла из полосы»
/// (`band_exit_bps`). Позиция закрывается не позже дедлайна. `sigma_bps` —
/// `σ_H` касания для дедлайна формы (`lob::sigma`), нужна только σ-формам.
/// `None` — форму для этого касания не построить (см. `StopForm`);
/// вызывающий считает пропуск.
#[allow(clippy::cast_precision_loss)]
pub(crate) fn bounce_plan(
    touch: &TouchRecord,
    tick: f64,
    form: BounceForm,
    sigma_bps: Option<f64>,
    shape: PlanShape,
) -> Option<(i8, TradePlan)> {
    let PlanShape {
        lot,
        level_lot,
        post_only,
        trail_bps,
        trail_activate_bps,
        grid_legs,
        grid_step_ticks,
        entry_form,
        deadline_ns,
        early_exit_ns,
        entry_ttl,
        h3_usd,
        band_exit_bps,
        exit_form: _,
        entry_sigma_bps,
    } = shape;
    let p_tick = touch.price_tick;
    let p = p_tick as f64 * tick;
    // F5 (В-74): `entry_ttl_ns` — потолок срока жизни входа, а не таймер.
    // Прежний режим `touch` воспроизводит «вход до конца касания» байт в байт
    // и выключает условия F5 (`level_floor_qty`/`band_exit_bps` = 0) — на нём
    // стоит гейт «те же круги». `wall` — только условия рынка.
    let touch_ttl_ns = touch
        .end_ms
        .saturating_sub(touch.start_ms)
        .saturating_mul(1_000_000);
    let (entry_ttl_ns, level_floor_qty, band_exit_bps) = match entry_ttl {
        EntryTtl::Touch => (touch_ttl_ns, 0.0, 0.0),
        EntryTtl::Secs(secs) => (
            secs.saturating_mul(1_000_000_000),
            notional_floor_qty(h3_usd, p),
            band_exit_bps,
        ),
        EntryTtl::Wall => (i64::MAX, notional_floor_qty(h3_usd, p), band_exit_bps),
    };
    let grid_step_px = grid_step_ticks as f64 * tick;
    // Знак «в сторону от плотности»: для бида это вверх, для аска — вниз.
    // Стоп, тейк и прежний вход (`P ± 1` тик) считаются по нему.
    let away: i64 = match touch.side {
        Side::Bid => 1,
        Side::Ask => -1,
    };
    // Вход — от **первого фронтранера** касания (B2, В-58; спека §2: «заходят
    // либо в упор, либо от фронтрана» [D 02:35]). Цена фронтрана уже лежит по
    // правильную сторону уровня — для бида выше, для аска ниже, — поэтому знак
    // ей не нужен. Фронтрана впереди не было — прежний `P + 1` тик.
    //
    // F6 (В-73): у формы-лестницы вход — её ноги от `from` до `to` bps над
    // стеной; опорная цена для стопа/тейка — средняя цена лестницы **по
    // долям** (фактическую среднюю ведёт стратегия после F4 и сдвигает по ней
    // уровни). Прежняя форма (`single@fr`) берёт цену фронтранера байт в байт.
    let (ladder, entry_tick) = match entry_form {
        EntryForm::SingleFrontrun => (
            EntryLadder::NONE,
            touch
                .frontrun_tick
                .unwrap_or_else(|| p_tick.saturating_add(away)),
        ),
        // T-35 (Г-85): отступ от фронтрана в тиках, `ticks > 0` — к рынку; не ближе `P ± 1` к стене.
        EntryForm::SingleFrontrunOffset { ticks } => {
            let base = touch
                .frontrun_tick
                .unwrap_or_else(|| p_tick.saturating_add(away));
            let tick = base.saturating_add(away.saturating_mul(i64::from(ticks)));
            let tick = if tick.saturating_sub(p_tick).saturating_mul(away) < 1 {
                p_tick.saturating_add(away)
            } else {
                tick
            };
            (EntryLadder::NONE, tick)
        }
        EntryForm::Ladder {
            legs,
            from_bps,
            to_bps,
            wall_weight,
        } => {
            let (ladder, avg_tick) = ladder_legs(p_tick, away, legs, from_bps, to_bps, wall_weight);
            (ladder, avg_tick)
        }
        // В-131: ноги — σ монеты на взводе × (a…b) bps, нераздельные (каждая ≥ 1 тика дальше предыдущей,
        // первая не ближе `P ± 1`); σ нет — плана нет (`n_no_sigma` у сетки).
        EntryForm::LadderSigma {
            legs,
            from_sigma,
            to_sigma,
            wall_weight,
        } => {
            let sigma = entry_sigma_bps?;
            ladder_legs_distinct(
                p_tick,
                away,
                legs,
                from_sigma * sigma,
                to_sigma * sigma,
                wall_weight,
            )
        }
        // T4 (В-73-подобно, П-02, Г-86): рыночный вход — лимит за
        // `MARKET_CROSS_MARGIN_BPS` от стены, гарантированно пересекающий
        // спред (симулятор идёт по стакану до фактической цены фила —
        // дальше этой цели, не по ней).
        EntryForm::Market => (
            EntryLadder::NONE,
            p_tick + away * bps_to_ticks_ceil(MARKET_CROSS_MARGIN_BPS, p_tick),
        ),
    };
    // T4 (Г-86): рыночный вход не может быть мейкером — GTX отклонил бы
    // заявку, пересёкшую спред, как Expired (В-72); GTC независимо от
    // `--post-only`/набора.
    let post_only = if matches!(entry_form, EntryForm::Market) {
        false
    } else {
        post_only
    };
    // Расстояние от уровня «в сторону от плотности», тики (> 0 — по нужную сторону).
    let dist_from_level = (entry_tick - p_tick) * away;
    let stop_tick = match form.stop {
        StopForm::Before => {
            if dist_from_level < 2 {
                return None;
            }
            p_tick + away
        }
        StopForm::At => p_tick,
        StopForm::Behind => p_tick - away,
        StopForm::MidFrontrun => {
            if dist_from_level < 2 {
                return None;
            }
            p_tick + away * (dist_from_level / 2)
        }
        StopForm::BehindStack => touch.stack_next_tick? - away,
        StopForm::Pct(pct) => entry_tick - away * bps_to_ticks_ceil(pct * 100.0, entry_tick),
        StopForm::Sigma(a) => {
            // `a × σ_H` от входа в сторону плотности, но не ближе тика **за**
            // плотностью (`P − 1` для бида): дальний из двух.
            let sigma = sigma_bps?;
            let by_sigma = entry_tick - away * bps_to_ticks_ceil(a * sigma, entry_tick);
            let floor = p_tick - away;
            if (by_sigma - floor) * away > 0 {
                floor
            } else {
                by_sigma
            }
        }
    };
    // Стоп обязан лежать по «свою» сторону от входа — иначе форма вырождена.
    if (entry_tick - stop_tick) * away <= 0 {
        return None;
    }
    // Трейл (форма тейка): активация и откат в bps от входа; у остальных форм
    // — из `shape` (флаги `--trail-*` у `backtest --touches`).
    let (trail_bps, trail_activate_bps) = match form.take {
        TakeForm::Trail {
            activate_pct,
            trail_pct,
        } => (trail_pct * 100.0, activate_pct * 100.0),
        _ => (trail_bps, trail_activate_bps),
    };
    let take_tick = match form.take {
        // 1:1 **от входа** (спека §3: «самый базовый, это один к одному» [D 16:17]).
        // У трейла фиксированного тейка нет — цена стоит символически на 1:1,
        // стратегия её не читает при `trail_bps > 0`.
        TakeForm::OneToOne
        | TakeForm::Trail { .. }
        | TakeForm::HalfOneToOne
        | TakeForm::Eaten { .. } => entry_tick + (entry_tick - stop_tick),
        // Тейк в % от входа в сторону отскока — как `pct<x>` у стопа, зеркально.
        TakeForm::Pct(x) => entry_tick + away * bps_to_ticks_ceil(x * 100.0, entry_tick),
        TakeForm::Sigma(b) => {
            let sigma = sigma_bps?;
            let take_bps = (b * sigma)
                .max(form.take_floor_fees.unwrap_or(0.0) * crate::lob::costs::ROUNDTRIP_FEES_BPS);
            entry_tick + away * bps_to_ticks_ceil(take_bps, entry_tick)
        }
    };
    let entry_px = entry_tick as f64 * tick;
    let stop_px = stop_tick as f64 * tick;
    let take_px = take_tick as f64 * tick;
    let sigma = match touch.side {
        Side::Bid => SIGMA_LONG,
        Side::Ask => SIGMA_SHORT,
    };
    Some((
        sigma,
        TradePlan::Bounce {
            entry_px,
            stop_px,
            take_px,
            deadline_ns,
            entry_ttl_ns,
            // F5 (В-74): потолок срока жизни входа плюс два условия снятия —
            // размер на цене уровня ниже порога В-66 (в единицах крейта) и
            // уход лучшей цены нашей стороны за полосу лестницы.
            level_floor_qty,
            band_exit_bps,
            post_only,
            trail_bps,
            trail_activate_bps,
            grid_legs,
            grid_step_px,
            // F6 (В-73): ноги лестницы формы — целые тики и доли; у
            // `single@fr` набор пуст (`EntryLadder::NONE`) и работает прежний
            // путь `entry_px`/`grid_*` — гейт «те же круги».
            ladder,
            early_exit_ns,
            // Уровень касания `P` и шаг цены — данные для решения «уровень ещё
            // держит» (B4): стратегия не знает ни тика, ни цены уровня, они
            // приходят планом, как и всё остальное в нём.
            level_px: p,
            tick_px: tick,
            // E7: доля на тейке и пороги съедания — от формы тейка; размер
            // плотности на сигнале — из касания (лоты × шаг лота).
            take_frac: match form.take {
                TakeForm::HalfOneToOne => 0.5,
                _ => 1.0,
            },
            eaten_half_pct: match form.take {
                TakeForm::Eaten { half_pct, .. } => half_pct,
                _ => 0.0,
            },
            eaten_all_pct: match form.take {
                TakeForm::Eaten { all_pct, .. } => all_pct,
                _ => 0.0,
            },
            eaten_half_frac: match form.take {
                TakeForm::Eaten { .. } => 0.5,
                _ => 0.0,
            },
            level_qty: touch.size_at_touch.max(0) as f64 * level_lot.unwrap_or(lot),
            lot_qty: lot,
            // F7 (Б-75): форма выхода — из оси сетки (`--exit-form`).
            exit_eat_pct: match shape.exit_form {
                crate::commands::lob::bounce_grid::ExitForm::Eat { pct } => pct,
                _ => 0.0,
            },
            exit_gone_pct: match shape.exit_form {
                crate::commands::lob::bounce_grid::ExitForm::Gone { pct }
                | crate::commands::lob::bounce_grid::ExitForm::GoneTrail { pct, .. }
                | crate::commands::lob::bounce_grid::ExitForm::GoneBe { pct, .. }
                | crate::commands::lob::bounce_grid::ExitForm::GoneWall { pct, .. } => pct,
                _ => 0.0,
            },
            // Перенос стопа после снятия: безубыток (владелец 23.09) или уровень стены (26.09).
            gone_stop: match shape.exit_form {
                crate::commands::lob::bounce_grid::ExitForm::GoneBe { hard, .. } => {
                    crate::lob::strategy::GoneStop::Breakeven { hard }
                }
                crate::commands::lob::bounce_grid::ExitForm::GoneWall {
                    mode, buffer_bps, ..
                } => crate::lob::strategy::GoneStop::Wall { mode, buffer_bps },
                _ => crate::lob::strategy::GoneStop::Off,
            },
            // TK-014 `weat*`: съедание стены после входа, причина по ходу BTC.
            wall_eat: match shape.exit_form {
                crate::commands::lob::bounce_grid::ExitForm::WallEat {
                    pct,
                    secs,
                    mode,
                    btc_bps,
                    btc,
                } => crate::lob::strategy::WallEatExit {
                    pct,
                    secs,
                    mode,
                    btc_bps,
                    btc,
                },
                _ => crate::lob::strategy::WallEatExit::OFF,
            },
            pyramid: match shape.exit_form {
                crate::commands::lob::bounce_grid::ExitForm::PyrEat { parts } => {
                    crate::lob::strategy::PyramidCfg {
                        eat_parts: parts,
                        fresh: false,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::PyrFresh { parts } => {
                    crate::lob::strategy::PyramidCfg {
                        eat_parts: parts,
                        fresh: true,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::PyrReinstall { n, u3 } => {
                    crate::lob::strategy::PyramidCfg {
                        reinstall_n: n,
                        reinstall_u3: u3,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::PyrNewWall { k, u3 } => {
                    crate::lob::strategy::PyramidCfg {
                        newwall_k: k,
                        newwall_u3: u3,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::HalfStop { q4 } => {
                    crate::lob::strategy::PyramidCfg {
                        half_stop: true,
                        half_q4: q4,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::HalfLevel { q4 } => {
                    crate::lob::strategy::PyramidCfg {
                        half_level: true,
                        half_q4: q4,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::TakeSched { g10, t4 } => {
                    crate::lob::strategy::PyramidCfg {
                        sched_g10: g10,
                        sched_t4: t4,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::Converge { tol, a_bps } => {
                    crate::lob::strategy::PyramidCfg {
                        converge_tol1: tol + 1,
                        converge_a_bps: a_bps,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::NoStop { x2 } => {
                    crate::lob::strategy::PyramidCfg {
                        nostop_x2: x2,
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                crate::commands::lob::bounce_grid::ExitForm::Wall2 { behind } => {
                    crate::lob::strategy::PyramidCfg {
                        wall2: if behind { 2 } else { 1 },
                        ..crate::lob::strategy::PyramidCfg::OFF
                    }
                }
                _ => crate::lob::strategy::PyramidCfg::OFF,
            },
            // Трейл после снятия (владелец 23.09): откат в bps от входа.
            gone_trail_bps: match shape.exit_form {
                crate::commands::lob::bounce_grid::ExitForm::GoneTrail { trail_pct, .. } => {
                    trail_pct * 100.0
                }
                _ => 0.0,
            },
        },
    ))
}

/// Вид записи подхода как касания (F6 этапа F, В-73): `bounce_plan`,
/// `TouchFilter` и `SignalWindows` читают касание, у подхода те же поля
/// называются иначе. `start_ms` — `arm_ms` (и взвод — это `t0` сигнала, и
/// фильтры возраста/минуты режима смотрят на взвод), `end_ms` — `disarm_ms`
/// (прежний режим входа `touch` для подхода = его жизнь), `size_at_touch` —
/// `size_at_arm`, `level_birth_ms` — рождение уровня, `touch_index` —
/// `approach_index`, `strength_e2` — сила кадра взвода (по ней работает порог
/// В-66), `flow_1h_lots` — оборот к взводу. Чего у подхода нет, то ноль:
/// `frontrun_tick: None` (ключ `--frontrun-only` подходы выбрасывает —
/// фронтрана на взводе не считаем), `traded_during` 0, `size_max_before` =
/// `size_at_arm` (ключ `eaten=` на подходах смысла не имеет — вызов с ним
/// отвергается), стопка — `stack_levels_at_arm` (Г-07, ключ `stack_min=`).
///
/// Единственное место отображения: `lob fill-capacity --targets approaches`
/// (F2) зовёт эту же функцию (аудит 21.09, В3 — прежде была вторая копия без
/// теста равенства, и они разошлись: Б3).
pub(crate) fn touch_view_of_approach(a: &ApproachRecord) -> TouchRecord {
    TouchRecord {
        side: a.side,
        price_tick: a.price_tick,
        touch_index: a.approach_index,
        start_ms: a.arm_ms,
        end_ms: a.disarm_ms,
        duration_ms: a.duration_ms(),
        level_birth_ms: a.level_birth_ms,
        size_at_touch: a.size_at_arm,
        size_max_before: a.size_at_arm,
        traded_during: 0,
        // T-35 (Г-85 на подходах, Исследователь 27.09): лоты впереди стены на взводе — ключ `frontrun_min=`;
        // цены первого фронтранера на взводе нет, поэтому `frontrun_tick` пуст и `single@fr` — `P ± 1`.
        frontrun_lots: a.frontrun_lots_at_arm,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: crate::lob::levels::round_zeros(a.price_tick),
        ended_by_death: false,
        stack_levels: a.stack_levels_at_arm,
        stack_next_tick: None,
        traded_first_s: [0; REACTION_WINDOWS_S.len()],
        flow_1h_lots: a.flow_1h_lots,
        strength_e2: a.strength_e2,
        strength_held_e2: [-1; STRENGTH_HELD_WINDOWS_S.len()],
        repeat_count: 0,
        depth_behind_lots: a.depth_behind_lots,
    }
}

/// План сделки по **записи подхода** (F6 этапа F, В-73): то же, что
/// `bounce_plan` для касания, но вход ставится на взводе
/// (`t0 = arm_ms`, стена — `price_tick`, срок жизни — до `disarm_ms` в режиме
/// `touch` или по F5). Числа формы те же: `single@fr` (у подхода фронтрана нет
/// — цена `P ± 1` тик, `--frontrun-only` такие сигналы выбрасывает) или
/// `ladder<N>x<from>..<to>[w<k>]`.
pub(crate) fn approach_plan(
    approach: &ApproachRecord,
    tick: f64,
    form: BounceForm,
    sigma_bps: Option<f64>,
    shape: PlanShape,
) -> Option<(i8, TradePlan)> {
    bounce_plan(
        &touch_view_of_approach(approach),
        tick,
        form,
        sigma_bps,
        shape,
    )
}

/// Целое отношение `a / b` двух положительных шагов; нет — `None`.
fn exact_ratio(a: i64, b: i64) -> Option<i64> {
    if a > 0 && b > 0 && a % b == 0 {
        Some(a / b)
    } else {
        None
    }
}

/// Тик сетки данных → тик действующей сетки (шаг в `k` раз крупнее): `dir > 0` — вверх, иначе вниз.
fn to_effective_tick(tick: i64, k: i64, dir: i64) -> i64 {
    if dir > 0 {
        -(-tick).div_euclid(k)
    } else {
        tick.div_euclid(k)
    }
}

/// `bounce_plan` на **действующей** сетке биржи (В-172, TK-037): книга, касания и уровни — в тиках и
/// лотах сетки данных (`grid_*_e9`, мельчайшая сетка суток), а шаги, действовавшие в момент касания
/// (`schedule.at(start_ms)`), могут быть крупнее. До смены шага ордера ставятся только на старой
/// цене и кратно старому лоту. Шаг сетки данных — единственный в этот момент (`k_p = k_q = 1`) —
/// прежний `bounce_plan` с теми же аргументами, байт в байт.
///
/// Иначе: цена уровня не кратна действующему шагу — `None` (вызывающий считает пропуск); фронтран и
/// «вторая плотность» пересчитаны в действующие тики с округлением в сторону уровня/от рынка;
/// `level_qty` прежний (`size_at_touch × лот сетки`), `lot_qty` и `tick_px` — действующие. Шаг
/// действующей сетки не кратен сетке данных — `None` (расписание из `StepSchedule::from_header`
/// такого не даёт). Работает на этапе плана, не в горячем пути стратегии.
#[allow(clippy::cast_precision_loss)]
pub(crate) fn bounce_plan_sched(
    touch: &TouchRecord,
    grid_tick_e9: i64,
    grid_lot_e9: i64,
    schedule: &StepSchedule,
    form: BounceForm,
    sigma_bps: Option<f64>,
    shape: PlanShape,
) -> Option<(i8, TradePlan)> {
    let (eff_tick_e9, eff_lot_e9) = schedule.at(touch.start_ms.saturating_mul(1_000_000));
    let k_p = exact_ratio(eff_tick_e9, grid_tick_e9)?;
    let k_q = exact_ratio(eff_lot_e9, grid_lot_e9)?;
    if k_p == 1 && k_q == 1 {
        return bounce_plan(touch, grid_tick_e9 as f64 / 1e9, form, sigma_bps, shape);
    }
    if touch.price_tick.rem_euclid(k_p) != 0 {
        return None;
    }
    let away: i64 = match touch.side {
        Side::Bid => 1,
        Side::Ask => -1,
    };
    let level = touch.price_tick / k_p;
    // Округление — в сторону `-away` (к уровню и от рынка): вход не агрессивнее цены, которую видели
    // (приоритета в очереди, которого не было, не выдумываем), а стоп «за второй плотностью» остаётся за ней.
    let eff_tick_of = |t: i64| to_effective_tick(t, k_p, -away);
    let mut eff = *touch;
    eff.price_tick = level;
    // Фронтран не ближе одного действующего тика к уровню — как прежний `P ± 1` без фронтрана.
    eff.frontrun_tick = touch.frontrun_tick.map(|f| {
        let r = eff_tick_of(f);
        if (r - level) * away < 1 {
            level + away
        } else {
            r
        }
    });
    eff.stack_next_tick = touch.stack_next_tick.map(eff_tick_of);
    let shape = PlanShape {
        lot: eff_lot_e9 as f64 / 1e9,
        level_lot: Some(shape.level_lot.unwrap_or(shape.lot)),
        ..shape
    };
    bounce_plan(&eff, eff_tick_e9 as f64 / 1e9, form, sigma_bps, shape)
}

/// `approach_plan` на действующей сетке биржи: вид подхода как касания и `bounce_plan_sched`
/// (время взвода — `arm_ms`).
pub(crate) fn approach_plan_sched(
    approach: &ApproachRecord,
    grid_tick_e9: i64,
    grid_lot_e9: i64,
    schedule: &StepSchedule,
    form: BounceForm,
    sigma_bps: Option<f64>,
    shape: PlanShape,
) -> Option<(i8, TradePlan)> {
    bounce_plan_sched(
        &touch_view_of_approach(approach),
        grid_tick_e9,
        grid_lot_e9,
        schedule,
        form,
        sigma_bps,
        shape,
    )
}

/// Строка отчёта: «профиль» — либо `все`, либо `ось:корзина`. Корзины —
/// существующие (`touch_axes`, `SIZE_LABELS`), новых границ здесь нет;
/// касание может попасть сразу в несколько строк, и это правильно: строки —
/// маргиналы осей, как в T37.
pub(super) struct BounceRow {
    pub(super) profile: String,
    pub(super) touches: Vec<usize>,
}

pub(super) fn bounce_rows(touches: &[TouchRecord], h3_lots: i64) -> Vec<BounceRow> {
    let mut rows = vec![BounceRow {
        profile: "все".to_string(),
        touches: (0..touches.len()).collect(),
    }];
    let push = |rows: &mut Vec<BounceRow>, axis: &str, bucket: &str, i: usize| {
        let name = format!("{axis}:{bucket}");
        match rows.iter_mut().find(|r| r.profile == name) {
            Some(r) => r.touches.push(i),
            None => rows.push(BounceRow {
                profile: name,
                touches: vec![i],
            }),
        }
    };
    for (i, t) in touches.iter().enumerate() {
        if let Some(b) = age_bucket(t.start_ms.saturating_sub(t.level_birth_ms)) {
            push(&mut rows, "возраст", b, i);
        }
        if h3_lots > 0 {
            if let Some(b) = size_bucket(t.size_at_touch as f64 / h3_lots as f64) {
                push(&mut rows, "размер", b, i);
            }
        }
        if let Some(share) = frontrun_share(t.frontrun_lots, t.size_at_touch) {
            if let Some(b) = frontrun_bucket(share) {
                push(&mut rows, "фронтран", b, i);
            }
        }
        push(&mut rows, "круглость", round_bucket(t.round_zeros), i);
        push(&mut rows, "номер", touch_index_bucket(t.touch_index), i);
        push(&mut rows, "сторона", side_name(t.side), i);
    }
    rows
}

/// Предрегистрированная сетка дедлайнов, секунды (B3, В-58 п. 4): ответ
/// владельца 2 — «S и S-D; S-D значит от секунд до, наверно, пары часов»;
/// 30 мин и 4 ч добавлены предрегистрацией 20.09 (S8 плана по сторонам),
/// 6 и 8 ч — В-198 (07.10, клетки x-deadline П-12).
const DEADLINES_S: [i64; 8] = [60, 600, 1_800, 3_600, 7_200, 14_400, 21_600, 28_800];

/// Предрегистрированный набор досрочных выходов, секунды (B4, В-58 п. 5):
/// «прилипание» касания дольше `X`. Отсутствие флага — четвёртый вариант той
/// же оси («выключен»), поэтому `None` здесь не ошибка, а значение сетки.
pub(super) const EARLY_EXITS_S: [i64; 3] = [1, 2, 3];

/// Дедлайн `--deadline-secs` → наносекунды: значение обязано быть из сетки
/// В-58 (она зафиксирована **до** данных, и «попробовать ещё одно» — отдельное
/// испытание, а не параметр), а сам горизонт — проходить общую проверку
/// `markout::check_horizons` (положительный и не длиннее суток). Вынесено
/// функцией, чтобы отказ проверялся тестом: на живом корне та же ошибка стоит
/// минут счёта до диагностики.
pub(crate) fn deadline_ns_from_secs(secs: i64) -> anyhow::Result<i64> {
    anyhow::ensure!(
        DEADLINES_S.contains(&secs),
        "--deadline-secs {secs} не из предрегистрированной сетки В-58 {DEADLINES_S:?}"
    );
    let ms = secs.saturating_mul(1_000);
    crate::lob::markout::check_horizons(&[ms]).map_err(anyhow::Error::msg)?;
    Ok(ms.saturating_mul(1_000_000))
}

/// Досрочный выход `--early-exit-secs` → наносекунды (B4): `None` — выключен,
/// `Some(X)` — `X` из набора В-58 {1, 2, 3}. Другое значение — отказ, тем же
/// правилом, что у дедлайна.
pub(crate) fn early_exit_ns_from_secs(secs: Option<i64>) -> anyhow::Result<i64> {
    let Some(x) = secs else {
        return Ok(0);
    };
    anyhow::ensure!(
        EARLY_EXITS_S.contains(&x),
        "--early-exit-secs {x} не из предрегистрированного набора В-58 {EARLY_EXITS_S:?}"
    );
    Ok(x.saturating_mul(1_000_000_000))
}
