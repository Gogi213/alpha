//! Ёмкость исполнения входа у стены (S10 шаг 1, владелец 20.09: «вход одной
//! заявкой на весь объём — х…, нужно частичное исполнение и лестница лимиток в
//! диапазоне, как в файлах „отскок“»; спека отскока §«вход»: раскидка
//! 0,2–0,7 % от стены, забирает часть [S 08:53]; половина у фронтрана,
//! половина глубже [D 05:54]).
//!
//! Замер **без движка**: что реально наторговано на каждом тике **перед**
//! стеной и сколько стояло в очереди на этих тиках к моменту постановки, —
//! из этого любой читатель (`tools/compute/fill-capacity.py`) считает, какая
//! доля лестницы из `N` ног на `$X` исполнилась бы правилом «наторговано на
//! тике ≥ очередь впереди + нога». Модель очереди крейта
//! (`RiskAdverseQueueModel`) зачитывает исполнение, когда уровень исчез из
//! книги, не различая «проторговали» и «сняли», — в 91 % сделок лонгов
//! выметенного не хватило бы на очередь впереди (`EXPERIMENTS.md` M13).
//! Здесь — консервативная противоположность: снятия чужих заявок впереди
//! нас не помогают, исполнение — только объёмом.
//!
//! Геометрия. Стена `P` (тик), «перед» стеной — в сторону рынка: для бида
//! выше `P`, для аска ниже. Полоса — тики `P`, `P ± 1`, …, `P ± K`, где
//! `K = ⌊P × band_bps / 10⁴⌋` (расстояние в bps переведено в целые тики
//! **вниз** — тик `K + 1` уже дальше полосы). Тик `0` — сама стена («в упор»,
//! [D 02:35]).
//!
//! Время. `t0` — старт касания (`TouchRecord::start_ms`), `end` — его конец
//! (там же снимается вход в бэктесте, `entry_ttl_ns`). Постановка — либо в
//! `t0` (как сейчас в стратегии), либо за `pre` до касания (лестница
//! практиков стоит **заранее**, пока цена идёт к стене). Слоты `post` —
//! постановка в `t0`, но заявка живёт `post` после старта касания независимо
//! от его конца (касание в записи длится 0,1 с в медиане — замер 20.09, и
//! вход, снимаемый в конце касания, почти не успевает исполниться объёмом):
//! очередь и лучшие цены у `post` — те же, что у `t0`, окно сделок —
//! `[t0, t0 + post]`; смерть стены слот не видит — верхняя оценка. Для
//! каждого окна `pre`, для `t0` и для каждого `post` на каждом тике полосы:
//!
//! - `queue` — лоты **нашей** стороны (бид у бид-стены) на тике по книге на
//!   **последнем кадре не позже** момента постановки: это очередь впереди
//!   нашей заявки. Кадра не позже момента в этих сутках нет (постановка до
//!   первого кадра файла) — `-1`, «неизвестно», не ноль.
//! - `sold` — лоты сделок **против** нашей стороны на тике (агрессор-продавец у
//!   бид-стены, агрессор-покупатель у аск-стены) по метке исполнения
//!   (`TradeHit::exch_ms`, как `traded_first_s`) за `[t0 − pre, t0)`; для
//!   `t0` — за `[t0, end]`; для `post` — за `[t0, t0 + post]`. Блочные и
//!   RPI-сделки не идут (видимую очередь не двигают, В-55).
//!
//! Ещё на тик — **момент выборки очереди** (владелец 20.09: исполнение «в упор»
//! бывает двух видов — стена обновилась и стоит, либо её проторговали насквозь и
//! цена ушла за неё; второе — отмена сценария): `clear_ms` — первая сделка после
//! `t0`, с которой наторговано на тике строго больше очереди `t0` (наш первый лот
//! исполнен), и лучшие цены обеих сторон на **первом кадре после** этой сделки
//! (`best_after_clear`): наша лучшая цена всё ещё на стене или выше — стена
//! держит; ниже стены — насквозь. `-1` — очередь не выбрана / кадра не было.
//!
//! Правило чтения: нога на тике `k`, поставленная за `pre`, исполнена на
//! `clamp(sold_pre + sold_touch − queue_pre, 0, нога)`; поставленная в `t0`
//! — на `clamp(sold_touch − queue_t0, 0, нога)`. Рядом — лучшие цены обеих
//! сторон на момент постановки: нога по нашу сторону от лучшей цены **другой**
//! стороны легла бы тейкером (или отвергнута пост-онли) — читатель решает,
//! считать ли её.
//!
//! Это офлайн-замер по касаниям из кэша, не горячий путь: цели известны до
//! реплея, память — `(K + 1) × (окон + 1)` целых на касание.

use crate::book::{Book, Side};
use crate::lob::levels::TradeHit;

/// Запас, на который цель остаётся активной после конца касания, мс: метки
/// книги (`cts_ms`) и ленты (`exch_ms`) — разные часы биржи, сделка с меткой
/// внутри окна может прийти после кадра с меткой за окном. Окно слота при этом
/// проверяется точно по метке сделки; запас — только чтобы её не потерять.
const ACTIVE_SLACK_MS: i64 = 60_000;

/// Одно касание-цель: стена, сторона, окно касания.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Target {
    pub side: Side,
    pub price_tick: i64,
    pub start_ms: i64,
    pub end_ms: i64,
}

impl Target {
    /// Знак «в сторону рынка от стены»: бид — вверх, аск — вниз.
    fn away(self) -> i64 {
        match self.side {
            Side::Bid => 1,
            Side::Ask => -1,
        }
    }

    /// Сторона сделок, которые исполняют нашу заявку: у бид-стены нас ест
    /// агрессор-продавец.
    fn hits_us(self, aggressor_is_buy: bool) -> bool {
        match self.side {
            Side::Bid => !aggressor_is_buy,
            Side::Ask => aggressor_is_buy,
        }
    }

    /// Другая сторона книги — её лучшая цена решает, пересекла бы нога спред.
    fn opposite(self) -> Side {
        match self.side {
            Side::Bid => Side::Ask,
            Side::Ask => Side::Bid,
        }
    }
}

/// Конец активности цели: конец касания или самое длинное окно `post` от старта.
fn active_until(t: Target, post_max_ms: i64) -> i64 {
    t.end_ms.max(t.start_ms.saturating_add(post_max_ms))
}

/// Ширина полосы в тиках для стены `price_tick`: `⌊P × band_bps / 10⁴⌋`, не
/// меньше нуля. `P` в тиках — расстояние `x` bps от цены `P × tick` равно
/// `P × x / 10⁴` тиков, шаг тика сокращается.
pub fn band_ticks(price_tick: i64, band_bps: i64) -> i64 {
    if price_tick <= 0 || band_bps <= 0 {
        return 0;
    }
    (price_tick as i128 * band_bps as i128 / 10_000)
        .try_into()
        .unwrap_or(i64::MAX)
}

/// Момент снимка книги: окно `pre` (индекс в `pre_ms`) или сам `t0`
/// (индекс `pre_ms.len()`); слоты `post` снимка не имеют — берут снимок `t0`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Snapshot {
    ts_ms: i64,
    target: usize,
    slot: usize,
}

/// Снимок лучших цен на момент постановки: наша сторона и другая; `-1` —
/// книги не было.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BestAt {
    pub ours: i64,
    pub opposite: i64,
}

impl BestAt {
    const UNKNOWN: BestAt = BestAt {
        ours: -1,
        opposite: -1,
    };
}

/// Замер по одному касанию: полоса тиков × слоты (`pre_ms`, `t0`, затем `post_ms`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TargetCapacity {
    pub target: Target,
    /// `K` — тиков в полосе сверх стены.
    pub band: i64,
    /// `queue[k × slots + s]` — очередь на тике `k` в слоте `s`; `-1` — нет кадра.
    queue: Vec<i64>,
    /// `sold[k × slots + s]` — наторговано против нас на тике `k` за окно слота.
    sold: Vec<i64>,
    /// Лучшие цены на момент каждого слота.
    best: Vec<BestAt>,
    /// Наторговано против нас на тике с `t0` без окна (пока цель активна).
    cum: Vec<i64>,
    /// Момент выборки очереди `t0` на тике, мс (`-1` — не выбрана).
    clear_ms: Vec<i64>,
    /// Лучшие цены на первом кадре после выборки очереди.
    best_after_clear: Vec<BestAt>,
}

impl TargetCapacity {
    fn new(target: Target, band: i64, slots: usize) -> Self {
        let n = usize::try_from(band).unwrap_or(0).saturating_add(1);
        Self {
            target,
            band,
            queue: vec![-1; n * slots],
            sold: vec![0; n * slots],
            best: vec![BestAt::UNKNOWN; slots],
            cum: vec![0; n],
            clear_ms: vec![-1; n],
            best_after_clear: vec![BestAt::UNKNOWN; n],
        }
    }

    fn slots(&self) -> usize {
        self.best.len()
    }

    /// Тик полосы по смещению `k` от стены (в сторону рынка).
    pub fn tick_at(&self, k: i64) -> i64 {
        self.target.price_tick + k * self.target.away()
    }

    /// Смещение тика от стены, если тик в полосе.
    fn offset_of(&self, tick: i64) -> Option<usize> {
        let k = (tick - self.target.price_tick) * self.target.away();
        (0..=self.band).contains(&k).then_some(k as usize)
    }

    /// Очередь на тике `k` в слоте `s` (`-1` — кадра не было).
    pub fn queue(&self, k: usize, s: usize) -> i64 {
        self.queue[k * self.slots() + s]
    }

    /// Наторговано против нас на тике `k` за окно слота `s`.
    pub fn sold(&self, k: usize, s: usize) -> i64 {
        self.sold[k * self.slots() + s]
    }

    /// Лучшие цены на момент слота `s`.
    pub fn best(&self, s: usize) -> BestAt {
        self.best[s]
    }

    /// Момент выборки очереди `t0` на тике `k`, мс; `-1` — не выбрана.
    pub fn clear_ms(&self, k: usize) -> i64 {
        self.clear_ms[k]
    }

    /// Лучшие цены на первом кадре после выборки очереди на тике `k`.
    pub fn best_after_clear(&self, k: usize) -> BestAt {
        self.best_after_clear[k]
    }
}

/// Трекер ёмкости: цели известны заранее, снимки книги берутся по ходу
/// реплея, сделки копятся по активным целям.
#[derive(Debug)]
pub struct CapacityTracker {
    /// Окна постановки до касания, мс, по возрастанию; слот `pre_ms.len()` — `t0`.
    pre_ms: Vec<i64>,
    /// Окна жизни заявки после старта касания, мс, по возрастанию; слоты
    /// `pre_ms.len() + 1 + j`.
    post_ms: Vec<i64>,
    out: Vec<TargetCapacity>,
    /// Снимки по возрастанию времени; `next_snap` — первый не взятый.
    snaps: Vec<Snapshot>,
    next_snap: usize,
    /// Цели по возрастанию `start_ms − pre_max`; `next_activate` — первая не
    /// активированная, `active` — идущие (окно `[start − pre_max, end +
    /// ACTIVE_SLACK_MS]`).
    order: Vec<usize>,
    next_activate: usize,
    active: Vec<usize>,
    /// Был ли хоть один кадр книги в этих сутках (иначе снимок — «неизвестно»).
    seen_frame: bool,
    /// Тики, у которых очередь выбрана, а кадра после ещё не было: `(цель, тик)`.
    pending_after: Vec<(usize, usize)>,
}

impl CapacityTracker {
    /// `pre_ms` — окна постановки до касания, `post_ms` — окна жизни заявки
    /// после старта касания (любой порядок, без повторов, положительные);
    /// `band_bps` — ширина полосы; `targets` — касания суток.
    pub fn new(
        pre_ms: &[i64],
        post_ms: &[i64],
        band_bps: i64,
        targets: &[Target],
    ) -> anyhow::Result<Self> {
        let sorted = |name: &str, xs: &[i64]| -> anyhow::Result<Vec<i64>> {
            let mut v: Vec<i64> = xs.to_vec();
            v.sort_unstable();
            v.dedup();
            anyhow::ensure!(v.len() == xs.len(), "окна {name} повторяются: {xs:?}");
            anyhow::ensure!(
                v.iter().all(|&p| p > 0),
                "окно {name} обязано быть положительным: {xs:?}"
            );
            Ok(v)
        };
        let pre = sorted("постановки", pre_ms)?;
        let post = sorted("жизни", post_ms)?;
        anyhow::ensure!(
            band_bps > 0,
            "полоса обязана быть положительной, bps: {band_bps}"
        );
        let slots = pre.len() + 1 + post.len();
        let pre_max = pre.last().copied().unwrap_or(0);
        let mut out = Vec::with_capacity(targets.len());
        let mut snaps = Vec::with_capacity(targets.len() * slots);
        for (i, t) in targets.iter().enumerate() {
            anyhow::ensure!(
                t.end_ms >= t.start_ms,
                "касание кончается раньше начала: {t:?}"
            );
            out.push(TargetCapacity::new(
                *t,
                band_ticks(t.price_tick, band_bps),
                slots,
            ));
            for (s, p) in pre.iter().enumerate() {
                snaps.push(Snapshot {
                    ts_ms: t.start_ms - p,
                    target: i,
                    slot: s,
                });
            }
            snaps.push(Snapshot {
                ts_ms: t.start_ms,
                target: i,
                slot: pre.len(),
            });
        }
        snaps.sort_by_key(|s| (s.ts_ms, s.target, s.slot));
        let mut order: Vec<usize> = (0..targets.len()).collect();
        order.sort_by_key(|&i| (targets[i].start_ms - pre_max, i));
        Ok(Self {
            pre_ms: pre,
            post_ms: post,
            out,
            snaps,
            next_snap: 0,
            order,
            next_activate: 0,
            active: Vec::new(),
            seen_frame: false,
            pending_after: Vec::new(),
        })
    }

    /// Окна постановки, мс, по возрастанию.
    pub fn pre_ms(&self) -> &[i64] {
        &self.pre_ms
    }

    /// Окна жизни после старта касания, мс, по возрастанию.
    pub fn post_ms(&self) -> &[i64] {
        &self.post_ms
    }

    fn pre_max(&self) -> i64 {
        self.pre_ms.last().copied().unwrap_or(0)
    }

    /// Активировать цели, чьё окно началось к `ts_ms`, снять кончившиеся.
    fn roll_active(&mut self, ts_ms: i64) {
        let pre_max = self.pre_max();
        while let Some(&i) = self.order.get(self.next_activate) {
            if self.out[i].target.start_ms - pre_max > ts_ms {
                break;
            }
            self.active.push(i);
            self.next_activate += 1;
        }
        let post_max = self.post_ms.last().copied().unwrap_or(0);
        let out = &self.out;
        self.active.retain(|&i| {
            active_until(out[i].target, post_max).saturating_add(ACTIVE_SLACK_MS) >= ts_ms
        });
    }

    fn take_snapshot(&mut self, snap: Snapshot, book: &Book) {
        let tc = &mut self.out[snap.target];
        let slots = tc.slots();
        if !self.seen_frame {
            return;
        }
        let t = tc.target;
        let best = BestAt {
            ours: match t.side {
                Side::Bid => book.best_bid_tick_opt(),
                Side::Ask => book.best_ask_tick_opt(),
            }
            .unwrap_or(-1),
            opposite: match t.opposite() {
                Side::Bid => book.best_bid_tick_opt(),
                Side::Ask => book.best_ask_tick_opt(),
            }
            .unwrap_or(-1),
        };
        // Снимок `t0` — он же снимок всех слотов `post` (постановка в `t0`).
        let last = if snap.slot == self.pre_ms.len() {
            slots - 1
        } else {
            snap.slot
        };
        for s in snap.slot..=last {
            tc.best[s] = best;
        }
        for k in 0..=tc.band {
            let tick = tc.tick_at(k);
            let q = book.qty_lots_at(t.side, tick);
            for s in snap.slot..=last {
                tc.queue[k as usize * slots + s] = q;
            }
        }
    }

    /// Перед применением обновления книги с меткой `next_ts_ms`: все снимки с
    /// моментом **раньше** этой метки берутся с текущей книги — она и есть
    /// последний кадр не позже момента (обновление ровно в момент снимка ещё
    /// войдёт в него). Зовётся на каждое обновление по порядку.
    pub fn before_update(&mut self, next_ts_ms: i64, book: &Book) {
        while let Some(&snap) = self.snaps.get(self.next_snap) {
            if snap.ts_ms >= next_ts_ms {
                break;
            }
            self.take_snapshot(snap, book);
            self.next_snap += 1;
        }
    }

    /// После применения обновления: книга есть, кадры пошли; тики с только что
    /// выбранной очередью получают лучшие цены этого кадра.
    pub fn after_update(&mut self, ts_ms: i64, book: &Book) {
        self.seen_frame = true;
        self.roll_active(ts_ms);
        for (i, k) in self.pending_after.drain(..) {
            let tc = &mut self.out[i];
            let t = tc.target;
            tc.best_after_clear[k] = BestAt {
                ours: match t.side {
                    Side::Bid => book.best_bid_tick_opt(),
                    Side::Ask => book.best_ask_tick_opt(),
                }
                .unwrap_or(-1),
                opposite: match t.opposite() {
                    Side::Bid => book.best_bid_tick_opt(),
                    Side::Ask => book.best_ask_tick_opt(),
                }
                .unwrap_or(-1),
            };
        }
    }

    /// Сделка ленты: копится на тик полосы каждой активной цели, если бьёт по
    /// нашей стороне и попадает в окно слота.
    pub fn observe_trade(&mut self, tr: TradeHit, book: &Book) {
        if tr.block || tr.rpi || tr.lots <= 0 {
            return;
        }
        // Снимки с моментом не позже сделки — с текущей книги: кадр `t0` уже
        // применён, а его снимок иначе ждал бы следующего кадра, и выборка
        // очереди на сделках между ними не считалась бы.
        while let Some(&snap) = self.snaps.get(self.next_snap) {
            if snap.ts_ms > tr.exch_ms {
                break;
            }
            self.take_snapshot(snap, book);
            self.next_snap += 1;
        }
        self.roll_active(tr.exch_ms);
        let npre = self.pre_ms.len();
        let active = std::mem::take(&mut self.active);
        for &i in &active {
            let tc = &mut self.out[i];
            let t = tc.target;
            if !t.hits_us(tr.aggressor_is_buy) {
                continue;
            }
            let Some(k) = tc.offset_of(tr.tick) else {
                continue;
            };
            let slots = tc.slots();
            let base = k * slots;
            if tr.exch_ms >= t.start_ms {
                // Выборка очереди `t0`: с этой сделки наторговано больше, чем
                // стояло впереди, — наш первый лот исполнен.
                tc.cum[k] = tc.cum[k].saturating_add(tr.lots);
                let q0 = tc.queue[base + npre];
                if tc.clear_ms[k] < 0 && q0 >= 0 && tc.cum[k] > q0 {
                    tc.clear_ms[k] = tr.exch_ms;
                    self.pending_after.push((i, k));
                }
                if tr.exch_ms <= t.end_ms {
                    tc.sold[base + npre] = tc.sold[base + npre].saturating_add(tr.lots);
                }
                for (j, &p) in self.post_ms.iter().enumerate() {
                    if tr.exch_ms <= t.start_ms.saturating_add(p) {
                        let s = base + npre + 1 + j;
                        tc.sold[s] = tc.sold[s].saturating_add(tr.lots);
                    }
                }
            } else {
                for (s, &p) in self.pre_ms.iter().enumerate() {
                    if tr.exch_ms >= t.start_ms - p {
                        tc.sold[base + s] = tc.sold[base + s].saturating_add(tr.lots);
                    }
                }
            }
        }
        self.active = active;
    }

    /// Конец суток: оставшиеся снимки — с последнего кадра (он и есть
    /// последний не позже любого более позднего момента).
    pub fn finish(mut self, book: &Book) -> Vec<TargetCapacity> {
        while let Some(&snap) = self.snaps.get(self.next_snap) {
            self.take_snapshot(snap, book);
            self.next_snap += 1;
        }
        self.out
    }
}

#[cfg(test)]
mod tests;
