//! Состояние R1 (TK-025) в трекере уровней: всё за флагом `LevelTracker::enable_r1`.
//!
//! Определения 22 колонок уровня — `docs/findings/tk025-design-2026-10-02.md`, имена и порядок —
//! `r1::LEVEL_NAMES`. Состояние уровня лежит в параллельной `SortedVec<(u8, i64), LiveR1>` рядом с
//! `live`, а не в `Live`: без флага структура `Live` и горячий путь прежние. Все массивы фиксированной
//! ёмкости, время — только из кадров и сделок, цена и размер — целые (`i128` в промежуточном счёте).
//!
//! Решения при разночтениях определения (записаны и в отчёт кодера B):
//! - момент — кадр взвода подхода (`arm_ms`), у всех подходов независимо от причины конца (как `ArmP08`):
//!   колонки считаются в кадре взвода и хранятся в `armed` до снятия подхода (касание, уход цены, смерть);
//! - интервал `wall_add_*`/`front_add_*` — переходы кадров от рождения уровня по кадр взвода включительно
//!   (на взводе интервала «взвод → t0» нет); при равных приростах остаётся первый кадр (как `time_to_max_ms`);
//! - кадр монеты — пара проходов трекера (бид, затем аск; `feed_frames_multi`): снимок потока и колонки
//!   считаются в конце аск-прохода, когда обе половины книги на метке кадра (бид/аск зеркальны, Н1);
//! - «зона между лучшей ценой и стеной» исключает оба конца: лучшую цену и сам уровень стены;
//! - `frontrun_levels` берётся с наблюдения `frontrun_before` (кадр за 1–2 с до t0), база
//!   `frontrun_delta_10s_lots` — последний кадр не позже `t0 − 10 с`; `frontrun(t0)` в дельте — это
//!   `frontrun_lots_at_arm` подхода (`Live::frontrun_before` на кадре взвода);
//! - сдвиг `born_shift_cbps` — от цены умершего уровня (старой цены), остальные cbps — от цены стены;
//! - `born_shift_lots` — размер новорождённого минус `size_max` умершего (по нему же идёт сопоставимость);
//! - отмены и счётчики окон `cancel_*` — по кадрам стороны стены; `since_far_ms` — по кадрам стороны стены
//!   (так же считает `far` подхода) с чужой лучшей ценой последнего кадра другой стороны.

use crate::lob::markout::HORIZONS_MS;
use crate::lob::r1::{ArmR1, LEVEL_N, R1_UNDEF};
use crate::lob::r1_flow::{BookView, R1Flow, BOOK_DEPTH};

use super::{
    classify_outcome, comparable_size, side_key, side_of, ApproachRecord, LevelObs, Live, Outcome, SortedVec,
    TradeHit, BPS_PER_UNIT, FRONTRUN_BACK_MS, LEVEL_MAP_CAPACITY,
};

/// Слотов секундного кольца отмен: окно до 3 с (`cancel_3s_lots`).
const SEC_SLOTS: usize = 3;
/// Слотов минутного кольца отмен: окно 60 мин (`cancel_60m_lots`).
const MIN_SLOTS: usize = 60;
/// Окно `cancel_60m_lots`, мс.
const CANCEL_LONG_MS: i64 = 3_600_000;
/// Слотов выборки фронтрана: секундные слоты на глубину 10 с (`frontrun_delta_10s_lots`) с запасом.
const FR_SLOTS: usize = 16;
/// Глубина памяти прошлых смертей монеты (`prev_death_*`), договор TK-025.
const DEATH_RING: usize = 256;
/// Ширина формы стека в тиках (`size_share15_bp`, `nz_levels15`), договор TK-025.
const SHAPE_TICKS: i64 = 15;
/// 1 cbps = bps × 100: доля × 10⁶ (определение единицы).
const CBPS_PER_UNIT: i128 = 1_000_000;
/// Горизонты хода лучшей цены после рождения: 1 с и 10 с — `HORIZONS_MS[1]`, `HORIZONS_MS[2]`.
const MOVE_HORIZONS_MS: [i64; 2] = [HORIZONS_MS[1], HORIZONS_MS[2]];

/// Занятых цен строго между лучшей ценой стороны и стеной: среди `better_n` цен лучше стены
/// лучшая цена стороны в зону не входит.
fn zone_n(better_n: u32) -> u32 {
    better_n.saturating_sub(1)
}

fn to_i64(v: i128) -> i64 {
    i64::try_from(v).unwrap_or(if v < 0 { i64::MIN + 1 } else { i64::MAX })
}

/// `num × 10⁶ / den` (cbps), `den <= 0` — не определено.
fn cbps(num: i128, den: i64) -> i64 {
    if den <= 0 {
        return R1_UNDEF;
    }
    to_i64(num * CBPS_PER_UNIT / i128::from(den))
}

/// Кольцо сумм по меткам слотов: слот `t mod N`, метка отличает живой слот от протухшего.
#[derive(Clone, Copy)]
struct TagRing<const N: usize> {
    tag: [i64; N],
    val: [i64; N],
}

impl<const N: usize> TagRing<N> {
    fn new() -> Self {
        Self {
            tag: [i64::MIN; N],
            val: [0; N],
        }
    }

    fn slot(t: i64) -> usize {
        t.rem_euclid(N as i64) as usize
    }

    fn add(&mut self, t: i64, v: i64) {
        let i = Self::slot(t);
        if self.tag[i] != t {
            self.tag[i] = t;
            self.val[i] = 0;
        }
        self.val[i] = self.val[i].saturating_add(v);
    }

    /// Сумма слотов `(t0 − w, t0]`.
    fn sum(&self, t0: i64, w: usize) -> i64 {
        debug_assert!(w <= N);
        let mut s = 0i64;
        for k in 0..w as i64 {
            let t = t0.saturating_sub(k);
            let i = Self::slot(t);
            if self.tag[i] == t {
                s = s.saturating_add(self.val[i]);
            }
        }
        s
    }
}

/// Слот выборки фронтрана: первое и последнее наблюдение шага `FRONTRUN_BACK_MS` — лоты строго лучше
/// стены и число занятых цен впереди неё. Те же границы слотов, что у `Live::slot_new/slot_old`.
#[derive(Clone, Copy)]
struct FrSlot {
    start_ms: i64,
    first_lots: i64,
    first_n: u32,
    last_ms: i64,
    last_lots: i64,
    last_n: u32,
}

impl FrSlot {
    fn new(ts_ms: i64, lots: i64, n: u32) -> Self {
        Self {
            start_ms: ts_ms,
            first_lots: lots,
            first_n: n,
            last_ms: ts_ms,
            last_lots: lots,
            last_n: n,
        }
    }
}

/// Кольцо слотов фронтрана уровня, новый слот — по тому же шагу, что в `Live::observe_frontrun`.
#[derive(Clone, Copy)]
struct FrRing {
    slots: [FrSlot; FR_SLOTS],
    newest: usize,
    len: usize,
}

impl FrRing {
    fn new(ts_ms: i64, lots: i64, n: u32) -> Self {
        Self {
            slots: [FrSlot::new(ts_ms, lots, n); FR_SLOTS],
            newest: 0,
            len: 1,
        }
    }

    /// Слот на `k` шагов старше нового (`k < len`).
    fn back(&self, k: usize) -> &FrSlot {
        &self.slots[(self.newest + FR_SLOTS - k) % FR_SLOTS]
    }

    fn observe(&mut self, ts_ms: i64, lots: i64, n: u32) {
        if ts_ms.saturating_sub(self.back(0).start_ms) >= FRONTRUN_BACK_MS {
            self.newest = (self.newest + 1) % FR_SLOTS;
            self.slots[self.newest] = FrSlot::new(ts_ms, lots, n);
            self.len = (self.len + 1).min(FR_SLOTS);
        } else {
            let s = &mut self.slots[self.newest];
            s.last_ms = ts_ms;
            s.last_lots = lots;
            s.last_n = n;
        }
    }

    /// Правило `Live::frontrun_before` (кадр не позже секунды до `ts_ms`): лоты и число цен впереди.
    fn before_1s(&self, ts_ms: i64) -> (i64, u32) {
        let new = self.back(0);
        if self.len < 2 {
            return (new.first_lots, new.first_n);
        }
        let old = self.back(1);
        if old.last_ms.saturating_add(FRONTRUN_BACK_MS) <= ts_ms {
            (old.last_lots, old.last_n)
        } else {
            (old.first_lots, old.first_n)
        }
    }

    /// Лоты впереди уровня на последнем наблюдении не позже `x`: последнее наблюдение самого нового слота,
    /// закончившегося до `x`, иначе первое наблюдение слота, начатого не позже `x` (слот накрывает `x`).
    /// Нет такого — `None` (уровень моложе).
    fn lots_at_or_before(&self, x: i64) -> Option<i64> {
        for k in 0..self.len {
            let s = self.back(k);
            if s.last_ms <= x {
                return Some(s.last_lots);
            }
            if s.start_ms <= x {
                return Some(s.first_lots);
            }
        }
        None
    }
}

/// Состояние R1 одного живого уровня.
#[derive(Clone, Copy)]
struct LiveR1 {
    /// `Live::traded` на прошлом наблюдении — сделки между кадрами для `cancel_*`.
    traded_seen: i64,
    cancel_sec: TagRing<SEC_SLOTS>,
    cancel_min: TagRing<MIN_SLOTS>,
    cancel_life: i64,
    /// Максимальный прирост размера стены за переход кадров с рождения, лоты и метка кадра (UNDEF — не было).
    wall_add_lots: i64,
    wall_add_ms: i64,
    /// То же для цен строго между лучшей ценой стороны и стеной.
    front_add_lots: i64,
    front_add_ms: i64,
    born_shift_cbps: i64,
    born_shift_lots: i64,
    /// Лучшая цена своей стороны на кадре рождения; `None` — рождение перенесено с прошлых суток.
    best_birth: Option<i64>,
    /// Лучшая цена своей стороны на последнем кадре не позже `birth + MOVE_HORIZONS_MS[k]`.
    best_at: [i64; 2],
    fr: FrRing,
    /// Метка последнего кадра, где чужая лучшая цена была дальше `2·D` от стены.
    last_far_ms: i64,
}

impl LiveR1 {
    fn new(ts_ms: i64, best_own: i64, carried: bool, lots: i64, n: u32) -> Self {
        Self {
            traded_seen: 0,
            cancel_sec: TagRing::new(),
            cancel_min: TagRing::new(),
            cancel_life: 0,
            wall_add_lots: 0,
            wall_add_ms: R1_UNDEF,
            front_add_lots: 0,
            front_add_ms: R1_UNDEF,
            born_shift_cbps: R1_UNDEF,
            born_shift_lots: R1_UNDEF,
            best_birth: (!carried).then_some(best_own),
            best_at: [best_own; 2],
            fr: FrRing::new(ts_ms, lots, n),
            last_far_ms: i64::MIN,
        }
    }
}

#[derive(Clone, Copy)]
struct Death {
    side: u8,
    tick: i64,
    ms: i64,
    /// 1 съеден, 0 снят, 2 между (правило 70/20).
    outcome: i64,
}

/// Последние `DEATH_RING` смертей монеты, кольцом; поиск — линейный от новых к старым.
struct DeathRing {
    items: [Death; DEATH_RING],
    next: usize,
    len: usize,
}

impl DeathRing {
    fn new() -> Self {
        Self {
            items: [Death {
                side: 0,
                tick: 0,
                ms: 0,
                outcome: 0,
            }; DEATH_RING],
            next: 0,
            len: 0,
        }
    }

    fn push(&mut self, d: Death) {
        self.items[self.next] = d;
        self.next = (self.next + 1) % DEATH_RING;
        self.len = (self.len + 1).min(DEATH_RING);
    }

    /// Самая поздняя смерть на этой стороне и цене.
    fn last(&self, side: u8, tick: i64) -> Option<Death> {
        for k in 0..self.len {
            let d = self.items[(self.next + DEATH_RING - 1 - k) % DEATH_RING];
            if d.side == side && d.tick == tick {
                return Some(d);
            }
        }
        None
    }
}

/// Наблюдение живого уровня в кадре своей стороны — вход `R1State::observe_level`.
pub(super) struct ObsCtx {
    pub ts_ms: i64,
    pub s: u8,
    pub tick: i64,
    pub size: i64,
    /// Размер на прошлом наблюдении (`Live::prev` до обновления).
    pub prev_size: i64,
    pub traded_total: i64,
    pub birth_ms: i64,
    pub better_lots: i64,
    pub better_n: u32,
    pub best_own: i64,
    pub best_opp: Option<i64>,
    pub d_bps: i64,
}

/// Подход, взведённый в кадре, — колонки заполняются в конце кадра монеты (`R1State::finish_frame`):
/// для ближайшего чужого уровня нужен весь `live`, который обход держит заимствованным.
#[derive(Clone, Copy)]
pub(super) struct PendingArm {
    pub ts_ms: i64,
    pub key: (u8, i64),
    pub birth_ms: i64,
    pub size: i64,
    /// `Live::frontrun_before` на кадре взвода (то же, что `ApproachRecord::frontrun_lots_at_arm`).
    pub frontrun_lots: i64,
}

/// Состояние R1 монеты: поток (`R1Flow`), снимки книги сторон, состояние уровней, память смертей.
pub(super) struct R1State {
    flow: Box<R1Flow>,
    /// Два буфера на сторону (текущий и прошлый кадр), переключаются `cur`: копии кадра нет.
    book: [[[(i64, i64); BOOK_DEPTH]; 2]; 2],
    book_len: [[usize; 2]; 2],
    cur: [usize; 2],
    live: SortedVec<(u8, i64), LiveR1>,
    deaths: DeathRing,
    start_ms: Option<i64>,
    pending: Vec<PendingArm>,
    /// Колонки взведённых подходов до снятия: ключ — уровень (подход уровня один).
    armed: SortedVec<(u8, i64), ArmR1>,
}

impl R1State {
    /// Выделение — один раз на трекер (`enable_r1`).
    pub(super) fn new() -> Box<Self> {
        Box::new(Self {
            flow: R1Flow::new(),
            book: [[[(0, 0); BOOK_DEPTH]; 2]; 2],
            book_len: [[0; 2]; 2],
            cur: [0; 2],
            live: SortedVec::with_capacity(LEVEL_MAP_CAPACITY),
            deaths: DeathRing::new(),
            start_ms: None,
            pending: Vec::with_capacity(8),
            armed: SortedVec::with_capacity(LEVEL_MAP_CAPACITY),
        })
    }

    #[inline(never)]
    pub(super) fn on_trade(&mut self, tr: &TradeHit) {
        self.flow.on_trade(tr);
    }

    /// Кадр стороны `s`: снимок книги (лучшая цена первой, до `BOOK_DEPTH` непустых цен); ход потока — на аск-проходе
    /// (парный бид-проход уже сохранён), чтобы обе половины книги были на метке кадра.
    #[inline(never)]
    pub(super) fn on_frame(&mut self, ts_ms: i64, s: u8, levels: &[LevelObs]) {
        if self.start_ms.is_none() {
            self.start_ms = Some(ts_ms);
        }
        let si = usize::from(s);
        let c = self.cur[si] ^ 1;
        self.cur[si] = c;
        let mut n = 0;
        for o in levels.iter().filter(|o| o.size_lots > 0).take(BOOK_DEPTH) {
            self.book[si][c][n] = (o.tick, o.size_lots);
            n += 1;
        }
        self.book_len[si][c] = n;
        if s == 0 {
            return;
        }
        let (cb, ca) = (self.cur[0], self.cur[1]);
        let view = BookView {
            bids: &self.book[0][cb][..self.book_len[0][cb]],
            asks: &self.book[1][ca][..self.book_len[1][ca]],
        };
        self.flow.on_frame(ts_ms, view);
    }

    /// Рождение уровня: заводит состояние.
    #[inline(never)]
    pub(super) fn birth(
        &mut self,
        key: (u8, i64),
        ts_ms: i64,
        best_own: i64,
        carried: bool,
        better: (i64, u32),
    ) {
        self.live.insert(
            key,
            LiveR1::new(ts_ms, best_own, carried, better.0, zone_n(better.1)),
        );
    }

    /// Подход уровня взведён в этом кадре: колонки — в конце кадра монеты. Прежние значения ключа стираются.
    #[inline(never)]
    pub(super) fn push_arm(&mut self, p: PendingArm) {
        self.armed.remove(&p.key);
        self.pending.push(p);
    }

    /// Наблюдение живого уровня в кадре своей стороны. Зовётся до обновления `Live::prev`.
    #[inline(never)]
    pub(super) fn observe_level(&mut self, finger: &mut usize, c: &ObsCtx) {
        let Self {
            live,
            book,
            book_len,
            cur,
            ..
        } = self;
        let Some(l) = live.get_mut_from(finger, &(c.s, c.tick)) else {
            return;
        };
        // Отмены: падение размера за вычетом исполненного против уровня между кадрами.
        let traded_d = c.traded_total.saturating_sub(l.traded_seen);
        l.traded_seen = c.traded_total;
        let cancel = c
            .prev_size
            .saturating_sub(c.size)
            .saturating_sub(traded_d)
            .max(0);
        if cancel > 0 {
            l.cancel_sec.add(c.ts_ms.div_euclid(1_000), cancel);
            l.cancel_min.add(c.ts_ms.div_euclid(60_000), cancel);
            l.cancel_life = l.cancel_life.saturating_add(cancel);
        }
        l.fr.observe(c.ts_ms, c.better_lots, zone_n(c.better_n));
        for (k, h) in MOVE_HORIZONS_MS.iter().enumerate() {
            if c.ts_ms.saturating_sub(c.birth_ms) <= *h {
                l.best_at[k] = c.best_own;
            }
        }
        if let Some(opp) = c.best_opp {
            let gap = if c.s == 0 { opp - c.tick } else { c.tick - opp };
            if i128::from(gap) * BPS_PER_UNIT
                > i128::from(c.d_bps.saturating_mul(2)) * i128::from(c.tick)
            {
                l.last_far_ms = c.ts_ms;
            }
        }
        let grow = c.size.saturating_sub(c.prev_size);
        if grow > l.wall_add_lots {
            l.wall_add_lots = grow;
            l.wall_add_ms = c.ts_ms;
        }
        let si = usize::from(c.s);
        let (cc, pc) = (cur[si], cur[si] ^ 1);
        let g = zone_growth_max(
            &book[si][cc][..book_len[si][cc]],
            &book[si][pc][..book_len[si][pc]],
            c.s == 0,
            c.tick,
        );
        if g > l.front_add_lots {
            l.front_add_lots = g;
            l.front_add_ms = c.ts_ms;
        }
    }

    /// Смерть уровня `key` (состояние `lv` до удаления): память смертей, снятие состояния, пометка
    /// новорождённых, которых это переустановка (то же правило, что `LevelRecord::repriced`).
    #[inline(never)]
    pub(super) fn on_death(
        &mut self,
        key: (u8, i64),
        lv: &Live,
        ts_ms: i64,
        newborns: &[(u8, i64, i64)],
    ) {
        // `armed` не трогаем: подход, оборванный этой смертью, снимается записью `LevelDeath` позже в том же
        // проходе и забирает свои колонки (`attach`); устаревшая запись заменяется на следующем `push_arm`.
        self.live.remove(&key);
        let outcome = match classify_outcome(lv.traded, lv.max) {
            Outcome::Eaten => 1,
            Outcome::Pulled => 0,
            Outcome::Mixed => 2,
        };
        self.deaths.push(Death {
            side: key.0,
            tick: key.1,
            ms: ts_ms,
            outcome,
        });
        for &(ns, nt, nsize) in newborns {
            let Some(d) = nt.checked_sub(key.1).filter(|d| d.abs() == 1) else {
                continue;
            };
            if ns != key.0 || !comparable_size(nsize, lv.max) {
                continue;
            }
            let Some(n) = self.live.get_mut(&(ns, nt)) else {
                continue;
            };
            if n.born_shift_cbps == R1_UNDEF {
                // Плюс — от цены: у бид-стены вниз, у аск-стены вверх.
                let away = if ns == 0 { -d } else { d };
                n.born_shift_cbps = cbps(i128::from(away), key.1);
                n.born_shift_lots = nsize.saturating_sub(lv.max);
            }
        }
    }

    /// Конец прохода: на аск-проходе (обе половины книги на метке кадра) заполняются колонки взведённых в
    /// этом кадре подходов обеих сторон. Уровень без состояния (рождён до `enable_r1` или умер в бид-проходе)
    /// оставляет подход без колонок.
    #[inline(never)]
    pub(super) fn finish_frame(&mut self, s: u8, live: &SortedVec<(u8, i64), Live>) {
        if s == 0 {
            return;
        }
        for i in 0..self.pending.len() {
            let p = self.pending[i];
            let Some(l) = self.live.get(&p.key) else {
                continue;
            };
            let mut r = ArmR1::undefined();
            self.flow.fill(side_of(p.key), p.ts_ms, &mut r.flow);
            r.level = self.level_columns(&p, l, live);
            self.armed.insert(p.key, r);
        }
        self.pending.clear();
    }

    /// Записи подходов, снятых в этом проходе: колонки взвода из `armed` (снятие стирает ключ).
    #[inline(never)]
    pub(super) fn attach(&mut self, emitted: &mut [ApproachRecord]) {
        for rec in emitted {
            if let Some(r) = self.armed.remove(&(side_key(rec.side), rec.price_tick)) {
                rec.r1 = Some(r);
            }
        }
    }

    fn level_columns(
        &self,
        c: &PendingArm,
        l: &LiveR1,
        live: &SortedVec<(u8, i64), Live>,
    ) -> [i64; LEVEL_N] {
        let t0 = c.ts_ms;
        let (s, wall) = c.key;
        let si = usize::from(s);
        let observed = self.start_ms.map_or(0, |st| t0.saturating_sub(st));
        let age = t0.saturating_sub(c.birth_ms);
        let mut o = [R1_UNDEF; LEVEL_N];
        // cancel_{1,3}s_lots: окно определено, если монета наблюдается не меньше окна.
        for (k, w) in [1usize, 3].into_iter().enumerate() {
            if observed >= (w as i64) * 1_000 {
                o[k] = l.cancel_sec.sum(t0.div_euclid(1_000), w);
            }
        }
        // cancel_60m_lots: и стена, и наблюдение монеты не моложе часа.
        if age >= CANCEL_LONG_MS && observed >= CANCEL_LONG_MS {
            o[2] = l.cancel_min.sum(t0.div_euclid(60_000), MIN_SLOTS);
        }
        o[3] = l.cancel_life;
        // wall_add_max_*, front_add_max_*: прироста не было — 0 и UNDEF.
        if l.wall_add_lots > 0 {
            o[4] = l.wall_add_lots;
            o[5] = t0.saturating_sub(l.wall_add_ms);
        } else {
            o[4] = 0;
        }
        if l.front_add_lots > 0 {
            o[6] = l.front_add_lots;
            o[7] = t0.saturating_sub(l.front_add_ms);
        } else {
            o[6] = 0;
        }
        o[8] = l.born_shift_cbps;
        o[9] = l.born_shift_lots;
        if let Some(d) = self.deaths.last(s, wall) {
            o[10] = t0.saturating_sub(d.ms);
            o[11] = d.outcome;
        }
        let cb = self.cur[si];
        if let Some((share, nz)) = shape15(
            &self.book[si][cb][..self.book_len[si][cb]],
            s == 0,
            wall,
            c.size,
        ) {
            o[12] = share;
            o[13] = nz;
        }
        if let Some(b0) = l.best_birth {
            for (k, h) in MOVE_HORIZONS_MS.iter().enumerate() {
                if age >= *h {
                    let d = i128::from(l.best_at[k]) - i128::from(b0);
                    o[14 + k] = cbps(if s == 0 { d } else { -d }, wall);
                }
            }
        }
        // Ближайший живой уровень чужой стороны.
        let mut nearest: Option<(i128, i64)> = None;
        for ((_, t), lv) in live.range((1 - s, i64::MIN), (1 - s, i64::MAX)) {
            let d = (i128::from(*t) - i128::from(wall)).abs();
            if nearest.is_none_or(|n| d < n.0) {
                nearest = Some((d, lv.seen_size));
            }
        }
        if let Some((d, size)) = nearest {
            o[16] = cbps(d, wall);
            if c.size > 0 {
                o[17] = to_i64(i128::from(size) * BPS_PER_UNIT / i128::from(c.size));
            }
        }
        let (fr_lots, fr_n) = l.fr.before_1s(t0);
        debug_assert_eq!(
            fr_lots, c.frontrun_lots,
            "кольцо R1 разошлось с Live::frontrun_before"
        );
        if let Some(base) = l.fr.lots_at_or_before(t0.saturating_sub(HORIZONS_MS[2])) {
            o[18] = c.frontrun_lots.saturating_sub(base);
        }
        o[19] = i64::from(fr_n);
        o[20] = t0.saturating_sub(l.last_far_ms.max(c.birth_ms));
        o
    }
}

/// Наибольший прирост размера за переход кадров на ценах строго между лучшей ценой стороны и стеной;
/// цена, которой не было на прошлом кадре, растёт от нуля. Оба списка — лучшая цена первой.
fn zone_growth_max(cur: &[(i64, i64)], prev: &[(i64, i64)], bid: bool, wall: i64) -> i64 {
    // Порядок «от лучшей цены вглубь»: у бида цена убывает, у аска растёт.
    let key = |t: i64| if bid { -t } else { t };
    let kw = key(wall);
    let mut best = 0i64;
    let mut pj = 0usize;
    for &(t, size) in cur.iter().skip(1) {
        if key(t) >= kw {
            break;
        }
        while pj < prev.len() && key(prev[pj].0) < key(t) {
            pj += 1;
        }
        let before = if pj < prev.len() && prev[pj].0 == t {
            prev[pj].1
        } else {
            0
        };
        best = best.max(size.saturating_sub(before));
    }
    best
}

/// Форма стека: `(размер стены · 10⁴ / сумма размеров цен в 15 тиках от лучшей, число занятых цен)`;
/// стена вне 15 тиков — `None`.
fn shape15(book: &[(i64, i64)], bid: bool, wall: i64, wall_size: i64) -> Option<(i64, i64)> {
    let best = book.first()?.0;
    let dist = |t: i64| if bid { best - t } else { t - best };
    if !(0..SHAPE_TICKS).contains(&dist(wall)) {
        return None;
    }
    let mut sum = 0i128;
    let mut nz = 0i64;
    for &(t, size) in book {
        if dist(t) >= SHAPE_TICKS {
            break;
        }
        sum += i128::from(size);
        nz += 1;
    }
    (sum > 0).then(|| (to_i64(i128::from(wall_size) * BPS_PER_UNIT / sum), nz))
}

#[cfg(test)]
mod unit_tests {
    use super::*;

    #[test]
    fn shape15_cuts_at_fifteen_ticks_and_rejects_far_wall() {
        // Бид: лучшая 100, стена 100 (9), 15 тиков = цены 100..86.
        let book = [(100, 9), (95, 3), (90, 5), (86, 2), (85, 40)];
        // Сумма в окне: 9+3+5+2 = 19; доля 9·10⁴/19 = 4736; занято 4.
        assert_eq!(shape15(&book, true, 100, 9), Some((4736, 4)));
        // Стена на расстоянии 15 тиков — вне окна.
        assert_eq!(shape15(&book, true, 85, 40), None);
        // Аск зеркален: лучшая 100.
        let ask = [(100, 9), (105, 3), (110, 5), (114, 2), (115, 40)];
        assert_eq!(shape15(&ask, false, 100, 9), Some((4736, 4)));
        assert_eq!(shape15(&ask, false, 115, 40), None);
        assert_eq!(shape15(&[], true, 100, 9), None);
    }

    #[test]
    fn zone_excludes_best_and_wall_and_counts_new_ticks_from_zero() {
        // Бид, стена 90: зона — цены 99..91, лучшая 100 и стена не входят.
        let prev = [(100, 4), (95, 3), (90, 10)];
        let cur = [(100, 9), (97, 4), (95, 5), (90, 30)];
        // 100: лучшая — нет; 97: новая, +4; 95: +2; 90: сама стена — нет.
        assert_eq!(zone_growth_max(&cur, &prev, true, 90), 4);
        // Аск, зеркально (200 − t): лучшая 100, цены 103, 105, стена 110.
        let prev_a = [(100, 4), (105, 3), (110, 10)];
        let cur_a = [(100, 9), (103, 4), (105, 5), (110, 30)];
        assert_eq!(zone_growth_max(&cur_a, &prev_a, false, 110), 4);
        // Стена — лучшая цена: зона пуста.
        assert_eq!(zone_growth_max(&[(90, 30)], &prev, true, 90), 0);
    }

    #[test]
    fn tag_ring_sums_window_and_drops_stale_slots() {
        let mut r = TagRing::<3>::new();
        r.add(5, 2);
        r.add(6, 3);
        r.add(6, 1);
        assert_eq!(r.sum(6, 1), 4);
        assert_eq!(r.sum(6, 3), 6);
        // Слот 8 переиспользует индекс слота 5: старое значение сброшено.
        r.add(8, 7);
        assert_eq!(r.sum(8, 3), 7 + 4);
        assert_eq!(r.sum(5, 1), 0);
    }

    #[test]
    fn fr_ring_picks_the_last_observation_not_after_the_cutoff() {
        // Шаг слота 1 с: кадры 1000, 2000, 3000 — по слоту на кадр, лоты 1, 2, 3.
        let mut r = FrRing::new(1_000, 1, 1);
        r.observe(2_000, 2, 2);
        r.observe(3_000, 3, 3);
        assert_eq!(r.lots_at_or_before(2_000), Some(2));
        assert_eq!(r.lots_at_or_before(2_500), Some(2));
        assert_eq!(r.lots_at_or_before(999), None);
        // Слот, накрывающий метку: берётся его первое наблюдение.
        let mut w = FrRing::new(1_000, 5, 1);
        w.observe(1_400, 6, 1);
        assert_eq!(w.lots_at_or_before(1_200), Some(5));
        assert_eq!(w.lots_at_or_before(1_400), Some(6));
    }

    #[test]
    fn death_ring_forgets_after_capacity_and_finds_latest() {
        let mut d = DeathRing::new();
        d.push(Death {
            side: 0,
            tick: 7,
            ms: 1,
            outcome: 0,
        });
        d.push(Death {
            side: 0,
            tick: 7,
            ms: 2,
            outcome: 1,
        });
        assert_eq!(d.last(0, 7).map(|x| x.ms), Some(2));
        assert!(d.last(1, 7).is_none());
        for i in 0..DEATH_RING as i64 {
            d.push(Death {
                side: 0,
                tick: 100 + i,
                ms: 10 + i,
                outcome: 2,
            });
        }
        assert!(d.last(0, 7).is_none(), "старше кольца — забыта");
        assert_eq!(d.last(0, 100).map(|x| x.ms), Some(10));
    }

    #[test]
    fn cbps_truncates_toward_zero_and_rejects_bad_reference() {
        assert_eq!(cbps(10, 10_000), 1_000);
        assert_eq!(cbps(-1, 9_999), -100);
        assert_eq!(cbps(1, 0), R1_UNDEF);
    }
}
