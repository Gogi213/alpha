//! Общие типы ядра (TK-492) — интерфейс между модулями `feat`, `episodes`, `control`, `reference`, `cli`.
//! Менять только вместе со всеми модулями. Смысл полей — П-34 (`docs/research/P-34-pump-onset-binance.md`).
//!
//! Время. Минута `m` — индекс от `GRID0_MS` (как `Contract::first_min`); слот `s` — 5-минутный индекс от
//! `GRID0_MS` (`Contract::oi_first_slot`), слот `s` ↔ момент `t = 5·s` (минута). Все признаки момента `t`
//! используют свечи с индексом `≤ t` (close минуты `t` известен в `t`) и OI-слот `s − oi_lag_slots`
//! (0 — метка `t_ms` OI = конец периода; 1 — консервативно, П-34 §1 шаг 0; задаётся аргументом запуска).
//! Минуты без close (дыра, v = 0) — close тянется вперёд (`close_ff`, как TK-442), пока не появится первый close
//! контракта до него — `NaN`. Слот, где нужное OI — `NaN` (дыра) — признак `NaN`, правило не срабатывает,
//! момент не берётся ни в контроли, ни в эпизоды-лаги (П-34 §1: «минуты исключаются»).

/// Число признаков: индекс `f` = 0..=9 ↔ F1..F10 (П-34 §3). F2 (индекс 1) — только контроль.
pub const NF: usize = 10;
pub const F_OI_PCT: usize = 0;
pub const F_OI_USD: usize = 1;
pub const F_TURN: usize = 2;
pub const F_TRADES: usize = 3;
pub const F_TAKER: usize = 4;
pub const F_TOP_COUNT: usize = 5;
pub const F_TOP_SUM: usize = 6;
pub const F_GLOBAL: usize = 7;
pub const F_VR: usize = 8;
pub const F_FUND: usize = 9;

/// Куб оценок одного контракта на 5-минутной сетке: слоты `slot0 .. slot0 + n`.
#[derive(Debug, Clone, PartialEq)]
pub struct Cube {
    pub slot0: i64,
    pub n: usize,
    /// Окна, мин (из `features.windows_min`), `nw = windows.len()`.
    pub windows: Vec<u32>,
    /// `z[(f * nw + wi) * n + i]` — робастный z признака `f` окна `wi` в слоте `slot0 + i`;
    /// знаковый для F5–F8, F10 (оба направления), для F1–F4, F9 — как есть (`|z|` вверх проверяет правило).
    /// `NaN` — не определено.
    pub z: Vec<f32>,
    /// `r[wi * n + i]` = `ln(c[t] / c[t − w])` (t = 5·(slot0+i), `close_ff`); `NaN` — не определено. Направление сигнала.
    pub r: Vec<f32>,
    /// σ 1 м за `controls.sigma_1m_window_days` суток до `t` включительно (ст. откл. минутных ln-доходностей
    /// `close_ff`; дыры не считаются); дециль режима для контролей берётся из него.
    pub sigma: Vec<f32>,
}

impl Cube {
    pub fn zat(&self, f: usize, wi: usize, i: usize) -> f32 {
        self.z[(f * self.windows.len() + wi) * self.n + i]
    }
    pub fn rat(&self, wi: usize, i: usize) -> f32 {
        self.r[wi * self.n + i]
    }
}

/// Правило П-34 §3/§6: семейство `fam` (0..=8 — одиночные F1,F3,F4,F5,F6,F7,F8,F9,F10 в порядке индексов признаков
/// без F2; 9 = S_oi = F1&F3; 10 = S_all = F1&F3&F4), окно `wi`, порог `ti` (индексы в `params`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Rule {
    pub fam: u8,
    pub wi: u8,
    pub ti: u8,
}

/// Эпизод тренда (П-34 §2): знак `sign` = +1 памп / −1 дамп, длительность-класс `di` (индекс в `episodes.D_min`),
/// `t0` — минута старта хода, `t_end` — минута конца (пика), `mv` — `ln`-ход, флаги — `FLAG_*`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Episode {
    pub di: u8,
    pub sign: i8,
    pub t0: i64,
    pub t_end: i64,
    pub mv: f64,
    pub flags: u8,
}
/// E4: ≥ `return_share` хода вернулось за ≤ `within_min` после пика (фитиль) — эпизод помечен, не удалён.
pub const FLAG_WICK: u8 = 1;
/// Хвост-выброс: хвост хода держится на меньше чем `outlier_tail_candles_lt` свечах (П-34 §8).
pub const FLAG_OUTLIER: u8 = 2;
/// Эпизод BTCUSDT того же знака и D перекрывает [t0, t_end] хотя бы наполовину (страта «рыночный», П-34 §2).
pub const FLAG_MARKET: u8 = 4;

/// Порог хода `X_D`: `x[di][0]` — вверх (памп), `x[di][1]` — вниз (дамп), в `ln`-ходе, из периода фита.
#[derive(Debug, Clone, PartialEq)]
pub struct XdTable {
    pub x: Vec<[f64; 2]>,
}

/// Лаг первого срабатывания правила к эпизоду (П-34 §4): `lag = t_f − t0` в минутах; `None` — не сработало в
/// `[t0 − L_pre, t0 + L_post]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LagRow {
    pub rule: Rule,
    pub episode: u32,
    pub lag: Option<i64>,
}

/// Контрольный момент (C2a/C2b/C1): минута `t`, знак, откуда.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Moment {
    pub t: i64,
    pub sign: i8,
}
