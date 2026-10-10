//! Состояние стратегии `StrategyState`, хвосты заявок и конструкторы.

use super::*;

/// Состояние одного круга одной стратегии на одном активе. `sigma` — сторона
/// этого круга (`SIGMA_LONG`/`SIGMA_SHORT`, `backtest::entry_side`),
/// назначается снаружи один раз при вооружении: какой именно сигнал
/// ловить — решение уровня `lob/levels`, не этого модуля (границы модулей,
/// `interfaces.md`). `plan` — чем этот круг торгует (см. `TradePlan`).
#[derive(Debug, Clone, PartialEq)]
pub struct StrategyState {
    pub(super) asset_no: usize,
    pub(super) sigma: i8,
    pub(super) qty: f64,
    pub(super) next_order_id: u64,
    pub(super) phase: Phase,
    pub(super) plan: TradePlan,
    /// Лучшая цена «в пользу позиции» с момента входа (для трейл-тейка): у
    /// лонга — максимум лучшего бида, у шорта — минимум лучшего аска.
    /// `0.0` — вход ещё не состоялся.
    pub(super) best_favourable: f64,
    /// Лучшая цена «в пользу позиции» **с момента снятия стены** — база трейла после снятия
    /// (`gone_trail_bps`). `0.0` — снятия ещё не было (трейл не взведён).
    pub(super) gone_peak: f64,
    /// Перенос стопа после снятия (`gone_stop`): снятие уже было (защёлка) и стоп уже перенесён.
    pub(super) gone_seen: bool,
    pub(super) gone_stop_active: bool,
    /// `wallk`: снятие застало позицию хуже цели — перенос отменён до конца сделки.
    pub(super) gone_stop_off: bool,
    /// E7: частичный выход уже был в этом круге (второй раз не делится).
    pub(super) partial_done: bool,
    /// Максимум размера плотности уровня с момента входа — база съедания
    /// «от максимума» (динамический режим чужих ботов). `0.0` — не считаем.
    pub(super) level_qty_max: f64,
    /// F7 (Б-75): размер плотности уровня в момент входа — база для `gone<W>`.
    pub(super) level_qty_at_entry: f64,
    /// F7 (Б-75): накопленные сделки в стену (против нашей позиции) с момента
    /// входа — база для `eat<X>`.
    pub(super) eaten_qty: f64,
    /// Накопленное исполнение входа и его стоимость — по ордерам крейта
    /// (`entry_snapshot`): средняя цена входа `entry_notional / entry_qty` и
    /// она же — база стопа и тейка (F4, В-78). Живут в состоянии, а не в
    /// `bot.position`: крейт обновляет позицию только на `Filled`.
    pub(super) entry_qty: f64,
    pub(super) entry_notional: f64,
    /// Накопленное исполнение выходов этого круга (F4, В-78): размер выхода —
    /// своя позиция `entry_qty − exit_qty`, а не плановый размер круга.
    pub(super) exit_qty: f64,
    /// Исполнение текущей заявки выхода, уже зачтённое в `exit_qty` (у заявки
    /// оно накапливается от отклика к отклику, а в позицию обязан войти
    /// прирост, а не вся сумма заново).
    pub(super) exit_accounted: f64,
    /// F8b (В5/Р2): сколько раз потолок `CANCEL_WAIT_NS` сорвал ожидание
    /// подтверждения отмены **лимитки выхода** — круг пошёл по плану, не
    /// дожидаясь биржи. Счётчик назван в артефактах (`n_exit_cancel_timeout`):
    /// предохранитель, который никто не видит, — это молчаливая потеря.
    pub(super) exit_cancel_timeouts: u64,
    /// F8c (К1): «сироты» — заявки, чьё снятие не подтвердилось за потолок
    /// `CANCEL_WAIT_NS`. Круг они не держат (потолок его освободил), но из
    /// рынка их никто не убрал: пока сирота стоит, снятие повторяется на
    /// каждом событии в любой фазе (`sweep_orphans`), а исполнение сироты не
    /// теряется — нога входа гасится по рынку, нога выхода зачитывается в
    /// `exit_qty`. Партии — массив фиксированной ёмкости (`OrphanCarry`, без
    /// кучи): вторая партия до ухода первой (лимитка выхода дважды не
    /// отменилась за потолок) не затирает первую (ревью 22.09, блокер 2);
    /// переполнение ёмкости считается (`orphan_overflow`), тогда вытесняется
    /// старейшая. Партии **переживают круг**: драйвер забирает их
    /// (`take_orphans`) и отдаёт следующему состоянию (`inherit_orphans`),
    /// иначе сирота входа, чей круг закрылся тем же событием, не снималась бы
    /// ни разу (ревью 22.09, блокер 1).
    pub(super) orphans: OrphanCarry,
    /// Остаток стоящих сирот **выхода** (по последней уборке): новые выходы
    /// сайзятся по свободной позиции `position − orphan_exit_open`, иначе
    /// исполнятся обе заявки и позиция уйдёт в перепрод (ревью 22.09, п. 4).
    pub(super) orphan_exit_open: f64,
    /// Сколько раз исполнение сироты было замечено — колонка `n_orphan_fills`.
    pub(super) orphan_fills: u64,
    /// Сколько раз новая партия сирот вытеснила живую из-за переполнения.
    pub(super) orphan_overflow: u64,
    /// TK-014 `weat*`: кольцо секундных корзин ёмкостью `W`; выделяется при постановке плана и
    /// только у формы `weat*` (иначе `None` — прежний путь).
    pub(super) wall_ring: Option<Box<[WallBucket]>>,
    /// TK-115 Г-112: кольцо ленты против позиции (журнал `tape_press`); `None` — флаг выключен, прежний путь.
    pub(super) tape_ring: Option<Box<[TapeBucket]>>,
    /// TK-115 Г-112: значение `tape_press` на момент исполнения входа (0 при выключенном кольце).
    pub(super) tape_at_fill: f64,
    /// TK-115 Г-116: кольцо отмен нашей стороны между `stop_px` и лучшей ценой (журнал `cxl_lots`); включается вместе с лентой.
    pub(super) cxl_ring: Option<Box<[TapeBucket]>>,
    /// TK-115 Г-116: снимок уровней нашей стороны и сделки против, пока исполнение входа не наступило.
    pub(super) cxl_book: Option<Box<CxlBook>>,
    /// TK-115 Г-116: значение `cxl_press` на момент исполнения входа (0 при выключенном кольце).
    pub(super) cxl_at_fill: f64,
    /// R2 (TK-065): крупные массивы добавок и известных стен — в куче и только у плана с `pyramid.on()`:
    /// клон состояния на каждом событии удержания (откат `fast_hold`) не таскает ≈ 200 Б, когда R2 выключен.
    pub(super) r2: Option<Box<R2Bufs>>,
    /// R2-A: число поставленных добавок (их id — `R2Bufs::add_ids`).
    pub(super) adds_done: u8,
    /// Позиция и стоимость базового входа в момент первой добавки; `Q0` — размер базы.
    pub(super) add_base_qty: f64,
    pub(super) add_base_notional: f64,
    /// Выход отправлен: добавки сняты и больше не ставятся.
    pub(super) adds_released: bool,
    /// Г-92: стена снята (ниже `level_floor_qty`) и ещё не вернулась; число возвратов; возврат ждёт добавки.
    pub(super) reinst_removed: bool,
    pub(super) reinstalls: u8,
    pub(super) reinst_trigger: bool,
    /// Г-92: стоп уже перенесён в безубыток (защёлка).
    pub(super) reinst_be_active: bool,
    /// Г-93: тики стен, существовавших к первому событию удержания (и уже использованных); снимок сделан.
    pub(super) nw_known_n: u8,
    pub(super) nw_init: bool,
    /// Г-114: половина по стопу уже закрыта (защёлка).
    pub(super) stop_half_done: bool,
    /// Г-106: стоп `nostop` переведён в безубыток (защёлка).
    pub(super) nostop_armed: bool,
    /// Г-133: момент начала погони на дедлайне (0 — не начата), цена стоящей лимитки погони и ожидание исполнения
    /// лимиткой от начала погони, нс (−1 — не исполнена лимиткой: вышли рынком или погони не было).
    pub(super) chase_start_ns: i64,
    pub(super) chase_px: f64,
    pub(super) chase_wait_ns: i64,
    /// Г-114 `halflevel`: после входа прошла сделка ленты за `level_px` (защёлка до конца круга).
    pub(super) level_broken: bool,
}

/// R2: id добавок (по порядку) и тики стен, существовавших к первому событию удержания.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct R2Bufs {
    pub(super) add_ids: [u64; MAX_ADDS],
    pub(super) nw_known: [i64; NW_KNOWN],
}

impl R2Bufs {
    pub(super) fn boxed() -> Box<Self> {
        Box::new(Self {
            add_ids: [0; MAX_ADDS],
            nw_known: [0; NW_KNOWN],
        })
    }
}

/// Ёмкость списка добавок на круг (`pyeat<N>` ограничено `N ≤ 10` ⇒ `K = N − 1 ≤ 9`).
pub const MAX_ADDS: usize = 9;

/// Чьи ноги стали сиротами (F8c, К1): исполнение ноги **входа** — лишняя
/// позиция, её гасят по рынку; исполнение ноги **выхода** — наш же выход, его
/// зачитывают в `exit_qty`, а не откупают обратно.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum OrphanKind {
    Entry,
    Exit,
}

/// Одна партия сирот: ноги `first .. first + legs` одной заявки/лестницы.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct OrphanBatch {
    pub(super) first: u64,
    pub(super) legs: u8,
    pub(super) kind: OrphanKind,
    /// Исполненное партии, уже учтённое (погашено или зачтено в выход).
    pub(super) accounted: f64,
}

/// Ёмкость партий сирот на состояние: вход + выход + запас; больше — считается
/// переполнением (`orphan_overflow`), не молчаливой потерей.
pub const MAX_ORPHAN_BATCHES: usize = 4;

/// Сироты, переносимые между кругами (F8c): драйвер бэктеста создаёт состояние
/// на сигнал и рвёт круг на `Idle`, поэтому партии отдаются наружу и
/// наследуются следующим состоянием; живой контур с долгоживущей стратегией
/// держит их внутри. `Copy` и без кучи — на пути события.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OrphanCarry {
    pub(super) batches: [OrphanBatch; MAX_ORPHAN_BATCHES],
    pub(super) n: u8,
}

impl OrphanCarry {
    pub const NONE: OrphanCarry = OrphanCarry {
        batches: [OrphanBatch {
            first: 0,
            legs: 0,
            kind: OrphanKind::Entry,
            accounted: 0.0,
        }; MAX_ORPHAN_BATCHES],
        n: 0,
    };

    pub fn is_empty(&self) -> bool {
        self.n == 0
    }

    /// Живых партий.
    pub fn len(&self) -> usize {
        usize::from(self.n)
    }

    /// Те же партии в нумерации другого прогона: номера заявок сдвинуты с базы `from` на базу
    /// `to` (память кругов сетки, G10: круг, взятый из памяти другого набора, отдаёт сирот в
    /// нумерации своего прогона — иначе номер сироты мог бы совпасть с заявкой следующего круга).
    /// Одна партия сирот входа (тест ключа памяти кругов, `lob::backtest`).
    #[cfg(test)]
    pub(crate) fn test_entry(first: u64, legs: u8) -> OrphanCarry {
        let mut c = OrphanCarry::NONE;
        c.batches[0] = OrphanBatch {
            first,
            legs,
            kind: OrphanKind::Entry,
            accounted: 0.0,
        };
        c.n = 1;
        c
    }

    pub fn rebased(mut self, from: u64, to: u64) -> OrphanCarry {
        for b in self.batches.iter_mut().take(usize::from(self.n)) {
            b.first = b.first.wrapping_sub(from).wrapping_add(to);
        }
        self
    }
}

impl StrategyState {
    /// Круг Decision 20: вход у спреда, выход на горизонте.
    pub fn new(asset_no: usize, sigma: i8, qty: f64, first_order_id: u64) -> Self {
        Self::with_plan(asset_no, sigma, qty, first_order_id, TradePlan::SpreadHold)
    }

    /// Круг с планом: цены и сроки сделки приходят снаружи (В-44).
    pub fn with_plan(
        asset_no: usize,
        sigma: i8,
        qty: f64,
        first_order_id: u64,
        plan: TradePlan,
    ) -> Self {
        Self {
            asset_no,
            sigma,
            qty,
            next_order_id: first_order_id,
            phase: Phase::Idle,
            plan,
            best_favourable: 0.0,
            gone_peak: 0.0,
            gone_seen: false,
            gone_stop_active: false,
            gone_stop_off: false,
            partial_done: false,
            level_qty_max: 0.0,
            level_qty_at_entry: 0.0,
            eaten_qty: 0.0,
            entry_qty: 0.0,
            entry_notional: 0.0,
            exit_qty: 0.0,
            exit_accounted: 0.0,
            exit_cancel_timeouts: 0,
            r2: match plan {
                TradePlan::Bounce { pyramid, .. } if pyramid.on() => Some(R2Bufs::boxed()),
                _ => None,
            },
            adds_done: 0,
            add_base_qty: 0.0,
            add_base_notional: 0.0,
            adds_released: false,
            reinst_removed: false,
            reinstalls: 0,
            reinst_trigger: false,
            reinst_be_active: false,
            nw_known_n: 0,
            nw_init: false,
            stop_half_done: false,
            nostop_armed: false,
            chase_start_ns: 0,
            chase_px: 0.0,
            chase_wait_ns: -1,
            level_broken: false,
            orphans: OrphanCarry::NONE,
            orphan_exit_open: 0.0,
            orphan_fills: 0,
            orphan_overflow: 0,
            tape_ring: None,
            tape_at_fill: 0.0,
            cxl_ring: None,
            cxl_book: None,
            cxl_at_fill: 0.0,
            wall_ring: match plan {
                TradePlan::Bounce { wall_eat, .. } if wall_eat.on() => {
                    Some(vec![WallBucket::EMPTY; wall_eat.secs as usize].into_boxed_slice())
                }
                _ => None,
            },
        }
    }
}
