//! Живой пул: состав символов пересматривается раз в период по REST-обороту без перезапуска процесса (G-022, аудит дыр сбора 16.11).
//!
//! До 16.11 пул считался один раз при старте: символ, чей оборот на старте был чуть ниже порога (или в топ-10), выпадал на весь запуск, а ушедший под порог
//! оставался в подписке до перезапуска (`docs/findings/collect-gap-audit-2026-11-16.md` §3.1). Теперь:
//! - `PoolState` — состав и гистерезис: символ входит при обороте ≥ порога, выходит, когда оборот ниже порога держится не меньше `hold` (24 ч);
//! - `Layout` — раскладка символов по шардам: новый символ докладывается в шард своего вида с местом (живая подписка на его соединениях) или в новый шард,
//!   ушедший снимается отпиской; шард не опустошается (последний поток остаётся);
//! - `PoolFile` — файл пула запуска `pool-<день>-<запуск>.json` (начальный состав, журнал изменений с метками времени, состав сейчас);
//! - `LivePool` — всё вместе: `apply` берёт свежий оборот и возвращает операции над шардами для `runner`, который их исполняет;
//! - `Rates` / `RateTracker` — измеренная нагрузка символов (кадров/с по видам) и `Weights` — оценка нагрузки для раскладки: новый символ ложится в шард с наименьшей
//!   нагрузкой под потолком биржи (`Exchange::shard_rate_cap`), крупнейшие символы начального плана — по разным шардам (`spread_plans`);
//! - `SavedState` — состояние пула (метки гистерезиса, нагрузка) в `pool-state.json` корня данных: перезапуск не теряет 24-часовую выдержку и измеренную нагрузку.
//!
//! Не горячий путь: вызывается раз в период из основного цикла процесса; кадры и записи не затрагиваются.

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::{json, Map, Value};

use super::exchange::{Exchange, ShardPlan, Turnover};
use super::health::{Eligible, PoolHealth};
use super::pool::rank_pool;
use super::writer::day_string;

/// Период REST-опроса оборота по умолчанию, с (назначаемое: 5 мин — новый символ теряет не больше этого от начала торговли выше порога).
pub const DEFAULT_REFRESH_S: u64 = 300;
/// Гистерезис выхода по умолчанию, с (приказ 16.11: символ держится не меньше суток после ухода под порог).
pub const DEFAULT_HOLD_S: u64 = 24 * 3600;
/// Ответ REST короче половины текущего состава считается сбоем (обрезанная выдача), пересмотр пропускается.
const SHORT_RESPONSE_RATIO: usize = 2;

/// Изменение состава пула.
#[derive(Clone, Debug, PartialEq)]
pub struct Change {
    pub ts_ms: i64,
    pub add: bool,
    pub symbol: String,
    /// Оборот за сутки по REST на момент изменения, $ (`None` — символа нет в ответе).
    pub qv: Option<f64>,
}

impl Change {
    fn json(&self) -> Value {
        json!({"ts_ms": self.ts_ms, "op": if self.add { "add" } else { "remove" }, "symbol": self.symbol,
               "qv": self.qv.map(|v| (v * 100.0).round() / 100.0)})
    }
}

/// Состав пула и гистерезис выхода.
pub struct PoolState {
    min_qv: f64,
    top_exclude: usize,
    hold_ms: i64,
    /// Символ → последний момент, когда его оборот был ≥ порога (или он был принят в пул).
    last_above: BTreeMap<String, i64>,
}

/// Итог одного пересмотра по REST: кого добавить и кого можно снять (держится под порогом не меньше `hold`).
#[derive(Debug, Default, PartialEq)]
pub struct Review {
    pub add: Vec<(String, f64)>,
    pub stale: Vec<(String, Option<f64>)>,
    /// Символов с оборотом ≥ порога по REST (после `top_exclude`).
    pub eligible: usize,
    /// Они же по именам: состав REST для независимой сверки «не подписано».
    pub names: Vec<String>,
}

impl PoolState {
    pub fn new(pool: &[String], now_ms: i64, min_qv: f64, top_exclude: usize, hold_s: u64) -> Self {
        Self {
            min_qv,
            top_exclude,
            hold_ms: i64::try_from(hold_s.saturating_mul(1000)).unwrap_or(i64::MAX),
            last_above: pool.iter().map(|s| (s.clone(), now_ms)).collect(),
        }
    }

    /// Как `new`, плюс символы прежнего запуска, которые держались под порогом меньше `hold` назад (`held`: символ → метка «последний раз выше порога», мс):
    /// выдержка не теряется при перезапуске. Вызывающий включает их в пул (`runner`).
    pub fn seeded(
        pool: &[String],
        now_ms: i64,
        min_qv: f64,
        top_exclude: usize,
        hold_s: u64,
        held: &BTreeMap<String, i64>,
    ) -> Self {
        let mut st = Self::new(pool, now_ms, min_qv, top_exclude, hold_s);
        for (s, t) in held {
            st.last_above.insert(s.clone(), *t);
        }
        st
    }

    pub fn len(&self) -> usize {
        self.last_above.len()
    }

    pub fn is_empty(&self) -> bool {
        self.last_above.is_empty()
    }

    /// Метки «последний раз выше порога» — в `pool-state.json`.
    pub fn marks(&self) -> &BTreeMap<String, i64> {
        &self.last_above
    }

    pub fn contains(&self, s: &str) -> bool {
        self.last_above.contains_key(s)
    }

    pub fn symbols(&self) -> Vec<String> {
        self.last_above.keys().cloned().collect()
    }

    /// Принять символ в пул (оборот сейчас ≥ порога).
    pub fn admit(&mut self, s: &str, now_ms: i64) {
        self.last_above.insert(s.to_string(), now_ms);
    }

    /// Снять символ (раскладка выполнила отписку).
    pub fn forget(&mut self, s: &str) {
        self.last_above.remove(s);
    }

    /// Сверка с оборотом по REST. Члены, чей оборот ≥ порога, обновляют метку; новые с оборотом ≥ порога — в `add`; члены, чей оборот ниже порога
    /// (или нет в ответе) держится не меньше `hold`, — в `stale`. Состав сам не меняется: `admit`/`forget` вызывает владелец после раскладки.
    /// `None` — ответ слишком короткий (обрезанная выдача биржи): пересмотр пропущен.
    pub fn review(&mut self, qv: &BTreeMap<String, f64>, now_ms: i64) -> Option<Review> {
        if qv.len() * SHORT_RESPONSE_RATIO < self.last_above.len() {
            return None;
        }
        let (names, _top) = rank_pool(qv, self.min_qv, self.top_exclude);
        let mut r = Review {
            eligible: names.len(),
            names: names.clone(),
            ..Review::default()
        };
        let eligible: std::collections::HashSet<&str> = names.iter().map(String::as_str).collect();
        for n in &names {
            let v = qv.get(n).copied().unwrap_or(0.0);
            match self.last_above.get_mut(n) {
                Some(t) => *t = now_ms,
                None => r.add.push((n.clone(), v)),
            }
        }
        for (s, t) in &self.last_above {
            if !eligible.contains(s.as_str()) && now_ms.saturating_sub(*t) >= self.hold_ms {
                r.stale.push((s.clone(), qv.get(s).copied()));
            }
        }
        Some(r)
    }
}

/// Вид шарда по имени: `book07` → `book`.
pub fn kind_of(name: &str) -> &str {
    name.trim_end_matches(|c: char| c.is_ascii_digit())
}

/// Шард раскладки: имя, вид, сколько потоков сейчас.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Slot {
    pub name: String,
    pub kind: String,
    pub streams: usize,
}

/// Операция над шардами; `slot` — индекс шарда в раскладке (он же индекс ручки в `runner`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Op {
    /// Дослать потоки в живой набор шарда.
    Append { slot: usize, streams: Vec<String> },
    /// Новый шард (мест в шардах вида нет); его индекс — `slot`.
    Spawn { slot: usize, plan: ShardPlan },
    /// Снять потоки с живого набора шарда.
    Remove { slot: usize, streams: Vec<String> },
}

/// Измеренная нагрузка символов: вид потока → символ (нижний регистр) → кадров/с (по обеим линиям шарда, как `msgs_10s` шарда; скользящее среднее по опросам).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Rates {
    by_kind: BTreeMap<String, HashMap<String, f64>>,
}

impl Rates {
    pub fn get(&self, kind: &str, lower: &str) -> Option<f64> {
        self.by_kind.get(kind)?.get(lower).copied()
    }

    pub fn set(&mut self, kind: &str, lower: &str, v: f64) {
        self.by_kind
            .entry(kind.to_string())
            .or_default()
            .insert(lower.to_string(), v);
    }

    pub fn is_empty(&self) -> bool {
        self.by_kind.values().all(HashMap::is_empty)
    }

    pub fn has_kind(&self, kind: &str) -> bool {
        self.by_kind.get(kind).is_some_and(|m| !m.is_empty())
    }

    pub fn len(&self) -> usize {
        self.by_kind.values().map(HashMap::len).sum()
    }

    pub fn to_json(&self) -> Value {
        let m: Map<String, Value> = self
            .by_kind
            .iter()
            .map(|(k, v)| {
                let sorted: BTreeMap<&String, f64> = v
                    .iter()
                    .map(|(s, r)| (s, (r * 100.0).round() / 100.0))
                    .collect();
                (k.clone(), json!(sorted))
            })
            .collect();
        Value::Object(m)
    }

    pub fn from_json(v: &Value) -> Self {
        let mut r = Self::default();
        if let Some(o) = v.as_object() {
            for (kind, m) in o {
                for (sym, x) in m.as_object().into_iter().flatten() {
                    if let Some(x) = x.as_f64().filter(|x| x.is_finite() && *x >= 0.0) {
                        r.set(kind, sym, x);
                    }
                }
            }
        }
        r
    }
}

/// Вес нового среднего относительно прежнего при каждом опросе (скользящее среднее).
const RATE_ALPHA: f64 = 0.5;
/// Опросы чаще не дают нового значения (счётчики кадров — приращение за период; короткий период шумит).
const RATE_MIN_DT_MS: i64 = 30_000;

/// Считает нагрузку символов по счётчикам кадров писателей (`WriterStats::seen`): приращение за период между опросами.
#[derive(Debug, Default)]
pub struct RateTracker {
    rates: Rates,
    prev: HashMap<(String, String), (i64, u64)>,
}

impl RateTracker {
    pub fn new(seed: Rates) -> Self {
        Self {
            rates: seed,
            prev: HashMap::new(),
        }
    }

    pub fn rates(&self) -> &Rates {
        &self.rates
    }

    /// `samples` — (вид потока, символ в нижнем регистре, кадров с начала процесса).
    #[allow(clippy::cast_precision_loss)]
    pub fn observe<'a>(
        &mut self,
        now_ms: i64,
        samples: impl IntoIterator<Item = (&'a str, &'a str, u64)>,
    ) {
        for (kind, sym, n) in samples {
            let key = (kind.to_string(), sym.to_string());
            match self.prev.get(&key).copied() {
                Some((t0, n0)) if now_ms - t0 >= RATE_MIN_DT_MS && n >= n0 => {
                    let inst = (n - n0) as f64 * 1000.0 / (now_ms - t0) as f64;
                    let v = match self.rates.get(kind, sym) {
                        Some(old) => RATE_ALPHA * inst + (1.0 - RATE_ALPHA) * old,
                        None => inst,
                    };
                    self.rates.set(kind, sym, v);
                    self.prev.insert(key, (now_ms, n));
                }
                Some(_) => {}
                None => {
                    self.prev.insert(key, (now_ms, n));
                }
            }
        }
    }
}

/// Оценка нагрузки символа для раскладки: измеренная (кадров/с), иначе оборот REST × измеренная нагрузка на $ того же вида; нет ни того ни другого — 0.
#[derive(Clone, Debug, Default)]
pub struct Weights {
    rates: Rates,
    qv_lower: HashMap<String, f64>,
    per_usd: BTreeMap<String, f64>,
}

impl Weights {
    pub fn new(rates: Rates, qv: &BTreeMap<String, f64>) -> Self {
        let qv_lower: HashMap<String, f64> =
            qv.iter().map(|(s, v)| (s.to_lowercase(), *v)).collect();
        let mut per_usd = BTreeMap::new();
        for (kind, m) in &rates.by_kind {
            let (mut r, mut q) = (0.0f64, 0.0f64);
            for (sym, rate) in m {
                if let Some(v) = qv_lower.get(sym).filter(|v| **v > 0.0) {
                    r += rate;
                    q += v;
                }
            }
            if r > 0.0 && q > 0.0 {
                per_usd.insert(kind.clone(), r / q);
            }
        }
        Self {
            rates,
            qv_lower,
            per_usd,
        }
    }

    /// Есть ли что-нибудь для раскладки (измерения или оборот).
    pub fn informative(&self) -> bool {
        !self.rates.is_empty() || !self.qv_lower.is_empty()
    }

    /// Нагрузка вида измерена: потолок биржи по кадрам/с применим (иначе веса — условные единицы оборота).
    pub fn measured(&self, kind: &str) -> bool {
        self.rates.has_kind(kind)
    }

    pub fn weight(&self, kind: &str, symbol: &str) -> f64 {
        let lower = symbol.to_lowercase();
        if let Some(r) = self.rates.get(kind, &lower) {
            return r;
        }
        let qv = self.qv_lower.get(&lower).copied().unwrap_or(0.0);
        // нет измерений вида — условные единицы: оборот в млн $ (важен только порядок)
        qv * self.per_usd.get(kind).copied().unwrap_or(1e-6)
    }
}

/// Раскладка символов по шардам: что и куда подписано.
pub struct Layout {
    slots: Vec<Slot>,
    by_symbol: BTreeMap<String, Vec<(usize, Vec<String>)>>,
    kinds: Vec<String>,
    per_conn: usize,
    weights: Weights,
}

impl Layout {
    /// По начальным планам процесса и пулу: каждый поток символа — в тот шард, где он оказался.
    pub fn new(
        exch: &dyn Exchange,
        plans: &[ShardPlan],
        pool: &[String],
        kinds: &[String],
        per_conn: usize,
    ) -> Self {
        let slots: Vec<Slot> = plans
            .iter()
            .map(|p| Slot {
                name: p.name.clone(),
                kind: kind_of(&p.name).to_string(),
                streams: p.streams.len(),
            })
            .collect();
        let mut at: HashMap<&str, usize> = HashMap::new();
        for (i, p) in plans.iter().enumerate() {
            for s in &p.streams {
                at.insert(s.as_str(), i);
            }
        }
        let mut by_symbol = BTreeMap::new();
        for sym in pool {
            let mut per_slot: BTreeMap<usize, Vec<String>> = BTreeMap::new();
            for sp in exch.plan(std::slice::from_ref(sym), kinds, per_conn) {
                if exch.pool_free_kinds().contains(&kind_of(&sp.name)) {
                    continue;
                }
                for s in sp.streams {
                    if let Some(&i) = at.get(s.as_str()) {
                        per_slot.entry(i).or_default().push(s);
                    }
                }
            }
            by_symbol.insert(sym.clone(), per_slot.into_iter().collect());
        }
        Self {
            slots,
            by_symbol,
            kinds: kinds.to_vec(),
            per_conn,
            weights: Weights::default(),
        }
    }

    pub fn slots(&self) -> &[Slot] {
        &self.slots
    }

    pub fn contains(&self, symbol: &str) -> bool {
        self.by_symbol.contains_key(symbol)
    }

    pub fn symbols(&self) -> usize {
        self.by_symbol.len()
    }

    /// Новые оценки нагрузки (после каждого опроса): по ним докладка выбирает шард.
    pub fn set_weights(&mut self, w: Weights) {
        self.weights = w;
    }

    pub fn weights(&self) -> &Weights {
        &self.weights
    }

    /// Оценка нагрузки каждого шарда: сумма весов его символов (кадров/с, когда вид измерен).
    pub fn loads(&self) -> Vec<f64> {
        let mut v = vec![0.0f64; self.slots.len()];
        for (sym, placed) in &self.by_symbol {
            for (i, _) in placed {
                if let (Some(slot), Some(x)) = (self.slots.get(*i), v.get_mut(*i)) {
                    *x += self.weights.weight(&slot.kind, sym);
                }
            }
        }
        v
    }

    /// Положить символ: потоки каждого вида — в шард этого вида с местом и наименьшей нагрузкой (измеренной; потолок биржи по кадрам/с не превышается), иначе в новый шард.
    pub fn add_symbol(&mut self, exch: &dyn Exchange, symbol: &str) -> Vec<Op> {
        let mut ops = Vec::new();
        let mut placed: Vec<(usize, Vec<String>)> = Vec::new();
        let loads = self.loads();
        for sp in exch.plan(&[symbol.to_string()], &self.kinds, self.per_conn) {
            let kind = kind_of(&sp.name).to_string();
            if exch.pool_free_kinds().contains(&kind.as_str()) || sp.streams.is_empty() {
                continue;
            }
            let cap = exch.shard_cap(&kind, self.per_conn);
            let n = sp.streams.len();
            let w_new = self.weights.weight(&kind, symbol);
            let rate_cap = exch
                .shard_rate_cap(&kind)
                .filter(|_| self.weights.measured(&kind));
            let load_of = |i: usize| loads.get(i).copied().unwrap_or(0.0);
            let best = self
                .slots
                .iter()
                .enumerate()
                .filter(|(i, s)| {
                    s.kind == kind
                        && s.streams + n <= cap
                        && rate_cap.is_none_or(|c| load_of(*i) + w_new <= c)
                })
                .min_by(|(i, s), (j, t)| {
                    load_of(*i)
                        .total_cmp(&load_of(*j))
                        .then(s.streams.cmp(&t.streams))
                        .then(i.cmp(j))
                })
                .map(|(i, _)| i);
            if let Some(i) = best {
                if let Some(s) = self.slots.get_mut(i) {
                    s.streams += n;
                }
                ops.push(Op::Append {
                    slot: i,
                    streams: sp.streams.clone(),
                });
                placed.push((i, sp.streams));
            } else {
                let next = self
                    .slots
                    .iter()
                    .filter(|s| s.kind == kind)
                    .filter_map(|s| s.name.strip_prefix(kind.as_str())?.parse::<usize>().ok())
                    .max()
                    .map_or(0, |m| m + 1);
                let name = format!("{kind}{next:02}");
                let i = self.slots.len();
                self.slots.push(Slot {
                    name: name.clone(),
                    kind,
                    streams: n,
                });
                let plan = ShardPlan {
                    name,
                    endpoint: sp.endpoint,
                    streams: sp.streams.clone(),
                };
                ops.push(Op::Spawn { slot: i, plan });
                placed.push((i, sp.streams));
            }
        }
        self.by_symbol.insert(symbol.to_string(), placed);
        ops
    }

    /// Снять символ. `None` — нельзя: у какого-то шарда это были бы все оставшиеся потоки (шард без потоков биржа закрывает); символ остаётся в подписке,
    /// пересмотр повторит попытку в следующий период.
    pub fn remove_symbol(&mut self, symbol: &str) -> Option<Vec<Op>> {
        let placed = self.by_symbol.get(symbol)?;
        let emptied = placed
            .iter()
            .any(|(i, st)| self.slots.get(*i).is_none_or(|s| s.streams <= st.len()));
        if emptied {
            return None;
        }
        let placed = self.by_symbol.remove(symbol)?;
        let mut ops = Vec::new();
        for (i, st) in placed {
            if let Some(s) = self.slots.get_mut(i) {
                s.streams = s.streams.saturating_sub(st.len());
            }
            ops.push(Op::Remove {
                slot: i,
                streams: st,
            });
        }
        Some(ops)
    }
}

/// Начальный план «по нагрузке»: символы каждого вида — от самого тяжёлого к лёгкому, каждый в шард этого вида с наименьшей нагрузкой, где есть место (LPT):
/// крупнейшие по нагрузке оказываются в разных шардах, а не там, куда их кладёт алфавит. Число шардов и их имена те же, что в `plans`; шарды видов без пула (`pool_free_kinds`) не меняются.
pub fn spread_plans(
    exch: &dyn Exchange,
    plans: &[ShardPlan],
    pool: &[String],
    kinds: &[String],
    per_conn: usize,
    w: &Weights,
) -> Vec<ShardPlan> {
    let mut out: Vec<ShardPlan> = plans.to_vec();
    let free = exch.pool_free_kinds();
    type Item = (f64, String, Vec<String>);
    let mut items: BTreeMap<String, Vec<Item>> = BTreeMap::new();
    for sym in pool {
        for sp in exch.plan(std::slice::from_ref(sym), kinds, per_conn) {
            let kind = kind_of(&sp.name);
            if free.contains(&kind) || sp.streams.is_empty() {
                continue;
            }
            items
                .entry(kind.to_string())
                .or_default()
                .push((w.weight(kind, sym), sym.clone(), sp.streams));
        }
    }
    struct Bin {
        slot: usize,
        load: f64,
        count: usize,
    }
    for (kind, mut list) in items {
        let mut bins: Vec<Bin> = out
            .iter()
            .enumerate()
            .filter(|(_, p)| kind_of(&p.name) == kind)
            .map(|(i, _)| Bin {
                slot: i,
                load: 0.0,
                count: 0,
            })
            .collect();
        if bins.is_empty() {
            continue;
        }
        for b in &bins {
            if let Some(p) = out.get_mut(b.slot) {
                p.streams.clear();
            }
        }
        let cap = exch.shard_cap(&kind, per_conn);
        list.sort_by(|a, b| b.0.total_cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
        for (wt, _sym, streams) in list {
            let n = streams.len();
            let pick = bins
                .iter()
                .enumerate()
                .filter(|(_, b)| b.count + n <= cap)
                .min_by(|(i, a), (j, b)| {
                    a.load
                        .total_cmp(&b.load)
                        .then(a.count.cmp(&b.count))
                        .then(i.cmp(j))
                })
                .or_else(|| bins.iter().enumerate().min_by_key(|(_, b)| b.count))
                .map(|(i, _)| i);
            if let Some(b) = pick.and_then(|i| bins.get_mut(i)) {
                b.load += wt;
                b.count += n;
                if let Some(p) = out.get_mut(b.slot) {
                    p.streams.extend(streams);
                }
            }
        }
    }
    out
}

/// Состояние пула между запусками: `<корень данных>/pool-state.json` — метки гистерезиса (символ → последний раз выше порога, мс) и измеренная нагрузка.
#[derive(Clone, Debug, Default)]
pub struct SavedState {
    pub ts_ms: i64,
    pub marks: BTreeMap<String, i64>,
    pub rates: Rates,
}

impl SavedState {
    pub fn path(root: &Path) -> PathBuf {
        root.join("pool-state.json")
    }

    pub fn load(root: &Path) -> Option<Self> {
        let v: Value =
            serde_json::from_str(&std::fs::read_to_string(Self::path(root)).ok()?).ok()?;
        let marks = v
            .get("last_above")?
            .as_object()?
            .iter()
            .filter_map(|(k, t)| Some((k.clone(), t.as_i64()?)))
            .collect();
        Some(Self {
            ts_ms: v.get("ts_ms").and_then(Value::as_i64).unwrap_or(0),
            marks,
            rates: v.get("rates").map(Rates::from_json).unwrap_or_default(),
        })
    }

    pub fn save(
        root: &Path,
        now_ms: i64,
        hold_s: u64,
        marks: &BTreeMap<String, i64>,
        rates: &Rates,
    ) -> std::io::Result<()> {
        super::health::write_atomic(
            &Self::path(root),
            &json!({"ts_ms": now_ms, "hold_s": hold_s, "last_above": marks, "rates": rates.to_json()}),
        )
    }

    /// Символы прежнего запуска, которых нет в пуле, но под порогом они держатся меньше `hold_s` и контракт ещё торгуется: их метки переходят в новый запуск,
    /// и перезапуск не сокращает 24-часовую выдержку.
    pub fn held(
        &self,
        pool: &[String],
        now_ms: i64,
        hold_s: u64,
        tradable: &BTreeMap<String, f64>,
    ) -> BTreeMap<String, i64> {
        let hold_ms = i64::try_from(hold_s.saturating_mul(1000)).unwrap_or(i64::MAX);
        let in_pool: std::collections::HashSet<&str> = pool.iter().map(String::as_str).collect();
        self.marks
            .iter()
            .filter(|(s, t)| {
                !in_pool.contains(s.as_str())
                    && now_ms.saturating_sub(**t) < hold_ms
                    && tradable.contains_key(*s)
            })
            .map(|(s, t)| (s.clone(), *t))
            .collect()
    }
}

/// Файл пула запуска: `pool-<день>-<запуск>.json` (по одному на запуск процесса — прежний `pool-<день>.json` перезаписывался следующим запуском того же дня и терял
/// пул предыдущих) и тот же документ под прежним именем `pool-<день>.json` для читателей, которые его открывают по имени.
pub struct PoolFile {
    per_run: PathBuf,
    compat: PathBuf,
    doc: Map<String, Value>,
    changes: Vec<Value>,
}

impl PoolFile {
    /// `base` — начальный документ пула (`ts_ms`, `min_quote_volume`, `top_excluded`, `pool`, `meta`/`qv` биржи): к нему добавляются запуск, журнал и состав сейчас.
    pub fn create(
        root: &Path,
        run_id: i64,
        base: Value,
        refresh_s: u64,
        hold_s: u64,
    ) -> std::io::Result<Self> {
        let day = day_string(run_id.div_euclid(86_400));
        let mut doc = match base {
            Value::Object(m) => m,
            _ => Map::new(),
        };
        let pool_now = doc.get("pool").cloned().unwrap_or(Value::Array(Vec::new()));
        doc.insert("run_id".into(), json!(run_id));
        doc.insert("refresh_s".into(), json!(refresh_s));
        doc.insert("hold_s".into(), json!(hold_s));
        doc.insert("pool_now".into(), pool_now);
        doc.insert(
            "updated_ms".into(),
            doc.get("ts_ms").cloned().unwrap_or(Value::Null),
        );
        let f = Self {
            per_run: root.join(format!("pool-{day}-{run_id}.json")),
            compat: root.join(format!("pool-{day}.json")),
            doc,
            changes: Vec::new(),
        };
        f.write()?;
        Ok(f)
    }

    fn write(&self) -> std::io::Result<()> {
        let mut d = self.doc.clone();
        d.insert("changes".into(), Value::Array(self.changes.clone()));
        let v = Value::Object(d);
        super::health::write_atomic(&self.per_run, &v)?;
        super::health::write_atomic(&self.compat, &v)
    }

    pub fn path(&self) -> &Path {
        &self.per_run
    }

    /// Дописать изменения, метаданные новых символов и состав сейчас; файл переписывается целиком (атомарно).
    pub fn record(
        &mut self,
        now_ms: i64,
        changes: &[Change],
        meta: &BTreeMap<String, Value>,
        pool_now: &[String],
    ) -> std::io::Result<()> {
        for c in changes {
            self.changes.push(c.json());
            if !c.add {
                continue;
            }
            if let Some(m) = meta.get(&c.symbol) {
                if let Some(Value::Object(dst)) = self.doc.get_mut("meta") {
                    dst.insert(c.symbol.clone(), m.clone());
                }
            }
            if let (Some(v), Some(Value::Object(dst))) = (c.qv, self.doc.get_mut("qv")) {
                dst.insert(c.symbol.clone(), json!((v * 100.0).round() / 100.0));
            }
        }
        self.doc.insert("pool_now".into(), json!(pool_now));
        self.doc.insert("updated_ms".into(), json!(now_ms));
        self.write()
    }
}

/// Итог одного применённого пересмотра.
#[derive(Debug, Default)]
pub struct Applied {
    pub ops: Vec<Op>,
    pub changes: Vec<Change>,
}

/// Живой пул процесса: состав, раскладка, файл, счётчики для health. Владеет им основной цикл `runner`; REST-опрос идёт в отдельном потоке.
pub struct LivePool {
    exch: Arc<dyn Exchange>,
    pub state: PoolState,
    pub layout: Layout,
    file: Option<PoolFile>,
    refresh_s: u64,
    hold_s: u64,
    last_try_ms: i64,
    last_ok_ms: i64,
    errors: u64,
    last_error: Option<String>,
    rest_symbols: usize,
    eligible: usize,
    /// Оборот символов, которых по REST надо подписать, но в подписке не было на момент опроса (до применения): сколько состав не успел поймать за период.
    missing_at_poll: Vec<(String, f64)>,
    added_total: u64,
    removed_total: u64,
    spawned_total: u64,
    blocked_remove: u64,
    /// Символ → с какого момента он обязан писать книгу (старт процесса или добавление пересмотром): основа независимой сверки «не подписано».
    since: BTreeMap<String, i64>,
    elig: Arc<Vec<Eligible>>,
    rates: RateTracker,
    /// Корень данных для `pool-state.json` (`None` — состояние не сохраняется).
    state_root: Option<PathBuf>,
}

impl LivePool {
    pub fn new(
        exch: Arc<dyn Exchange>,
        state: PoolState,
        layout: Layout,
        file: Option<PoolFile>,
        refresh_s: u64,
        hold_s: u64,
    ) -> Self {
        Self {
            exch,
            state,
            layout,
            file,
            refresh_s,
            hold_s,
            last_try_ms: 0,
            last_ok_ms: 0,
            errors: 0,
            last_error: None,
            rest_symbols: 0,
            eligible: 0,
            missing_at_poll: Vec::new(),
            added_total: 0,
            removed_total: 0,
            spawned_total: 0,
            blocked_remove: 0,
            since: BTreeMap::new(),
            elig: Arc::new(Vec::new()),
            rates: RateTracker::default(),
            state_root: None,
        }
        .started(super::events::now_ms())
    }

    /// Начальный состав обязан писать книгу с момента старта процесса.
    fn started(mut self, now_ms: i64) -> Self {
        self.since = self
            .state
            .symbols()
            .into_iter()
            .map(|s| (s, now_ms))
            .collect();
        self
    }

    /// Сохранять состояние пула (`pool-state.json`) в `root` после каждого опроса; `rates` — нагрузка символов из прежнего запуска.
    pub fn with_state(mut self, root: PathBuf, rates: Rates) -> Self {
        self.state_root = Some(root);
        self.rates = RateTracker::new(rates);
        self
    }

    /// Измерение нагрузки: `samples` — (вид потока, символ в нижнем регистре, кадров с начала процесса) по писателям шардов; зовётся перед `apply`.
    pub fn observe<'a>(
        &mut self,
        now_ms: i64,
        samples: impl IntoIterator<Item = (&'a str, &'a str, u64)>,
    ) {
        self.rates.observe(now_ms, samples);
    }

    /// Измеренная нагрузка символов (для начального плана и health).
    pub fn rates(&self) -> &Rates {
        self.rates.rates()
    }

    /// Сбой опроса REST: счётчик и текст — в health.
    pub fn failed(&mut self, now_ms: i64, e: &str) {
        self.last_try_ms = now_ms;
        self.errors += 1;
        self.last_error = Some(e.chars().take(200).collect());
    }

    /// Применить свежий оборот: пересмотр состава, раскладка (операции для шардов), журнал в файл пула. Операции исполняет вызывающий.
    pub fn apply(&mut self, t: &Turnover, now_ms: i64) -> Applied {
        self.last_try_ms = now_ms;
        let Some(rev) = self.state.review(&t.qv, now_ms) else {
            self.failed(now_ms, "ответ REST короче половины пула");
            return Applied::default();
        };
        self.last_ok_ms = now_ms;
        self.last_error = None;
        self.rest_symbols = t.qv.len();
        self.eligible = rev.eligible;
        self.layout
            .set_weights(Weights::new(self.rates.rates().clone(), &t.qv));
        self.elig = Arc::new(
            rev.names
                .iter()
                .map(|n| Eligible {
                    lower: n.to_lowercase(),
                    name: n.clone(),
                    qv: t.qv.get(n).copied().unwrap_or(0.0),
                    since_ms: *self.since.entry(n.clone()).or_insert(now_ms),
                })
                .collect(),
        );
        self.missing_at_poll = rev.add.clone();
        self.missing_at_poll
            .sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        let mut out = Applied::default();
        for (sym, qv) in &rev.add {
            self.state.admit(sym, now_ms);
            let ops = self.layout.add_symbol(self.exch.as_ref(), sym);
            self.spawned_total +=
                u64::try_from(ops.iter().filter(|o| matches!(o, Op::Spawn { .. })).count())
                    .unwrap_or(0);
            out.ops.extend(ops);
            self.added_total += 1;
            out.changes.push(Change {
                ts_ms: now_ms,
                add: true,
                symbol: sym.clone(),
                qv: Some(*qv),
            });
        }
        for (sym, qv) in &rev.stale {
            match self.layout.remove_symbol(sym) {
                Some(ops) => {
                    self.state.forget(sym);
                    self.since.remove(sym);
                    out.ops.extend(ops);
                    self.removed_total += 1;
                    out.changes.push(Change {
                        ts_ms: now_ms,
                        add: false,
                        symbol: sym.clone(),
                        qv: *qv,
                    });
                }
                None => self.blocked_remove += 1,
            }
        }
        if !out.changes.is_empty() {
            if let Some(f) = self.file.as_mut() {
                let _ = f.record(now_ms, &out.changes, &t.meta, &self.state.symbols());
            }
        }
        if let Some(root) = &self.state_root {
            let _ = SavedState::save(
                root,
                now_ms,
                self.hold_s,
                self.state.marks(),
                self.rates.rates(),
            );
        }
        out
    }

    /// Раздел health: что опрос REST видит против подписки.
    pub fn health(&self) -> PoolHealth {
        let eligible_now = self.eligible;
        let missing_now = self
            .missing_at_poll
            .iter()
            .filter(|(s, _)| !self.layout.contains(s))
            .count();
        let loads = self.layout.loads();
        let (mut max_load, mut max_name, mut over) = (0.0f64, String::new(), Vec::new());
        for (slot, l) in self.layout.slots().iter().zip(&loads) {
            if *l > max_load {
                max_load = *l;
                max_name = slot.name.clone();
            }
            let cap = self
                .exch
                .shard_rate_cap(&slot.kind)
                .filter(|_| self.layout.weights().measured(&slot.kind));
            if cap.is_some_and(|c| *l > c) {
                over.push(slot.name.clone());
            }
        }
        PoolHealth {
            refresh_s: self.refresh_s,
            hold_s: self.hold_s,
            last_try_ms: self.last_try_ms,
            last_ok_ms: self.last_ok_ms,
            errors: self.errors,
            last_error: self.last_error.clone(),
            rest_symbols: self.rest_symbols,
            eligible: eligible_now,
            not_in_layout: missing_now,
            not_in_layout_qv_usd: self
                .missing_at_poll
                .iter()
                .filter(|(s, _)| !self.layout.contains(s))
                .map(|(_, v)| *v)
                .sum(),
            elig: Arc::clone(&self.elig),
            missing_at_poll: self.missing_at_poll.len(),
            missing_at_poll_qv_usd: self.missing_at_poll.iter().map(|(_, v)| *v).sum(),
            missing_top: self.missing_at_poll.iter().take(10).cloned().collect(),
            added_total: self.added_total,
            removed_total: self.removed_total,
            spawned_shards: self.spawned_total,
            remove_blocked: self.blocked_remove,
            pool: self.state.len(),
            rates_tracked: self.rates.rates().len(),
            max_shard_load: max_load,
            max_shard: max_name,
            over_cap: over,
        }
    }

    /// Состав в нижнем регистре — для «символов с данными» на странице здоровья.
    pub fn lower_pool(&self) -> std::collections::HashSet<String> {
        self.state
            .symbols()
            .iter()
            .map(|s| s.to_lowercase())
            .collect()
    }
}
