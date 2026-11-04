//! Куб признаков F1…F10 на 5-минутной сетке (TK-492, П-34 §3, §6): робастные z «выхода за норму», направление
//! `r`, режим `sigma` и правила `fires` / `first_triggers`. Все значения слота `s` (момент `t = 5·s`) — по данным
//! `≤ t` (соглашения времени — шапка `types.rs`); заглядывания вперёд нет (тест: куб усечённого контракта равен
//! префиксу куба полного).
//!
//! # Допущения
//!
//! П-34 §3 не определяет ряд деталей; выбрано самое буквальное прочтение, новых чисел нет (всё из `Params`).
//! 1. **Сетка и область куба.** Слоты — от `⌈first_min/5⌉` до `⌊последняя минута свечей/5⌋`; OI вне диапазона
//!    свечей не используется. `grid_min.value` обязан равняться `SLOT_MIN` кэша, окна — кратны ему (иначе `assert!`).
//! 2. **Минимум истории — полное окно.** Окно нормы (`history_days` суток, у F10 `funding_z_history_days`) целиком
//!    лежит в данных самого признака: оно начинается не раньше первого определённого `x` (у F9 `x` определён лишь после
//!    `history_days` суток для `σ_H`, поэтому первый z — через `2·history_days`; у F10 — после первой выплаты), иначе
//!    `NaN`. Внутри окна значения `NaN` (дыры OI) в норму не входят; сколько значений осталось — не проверяется
//!    (порога доли нет в протоколе); нет ни одного — `NaN`. Окно самого признака (свечи, OI-слоты) тоже полное, иначе `NaN`.
//! 3. **Окно нормы кончается в `t − w`:** `z(t) = (x(t) − med)/(mad_factor·MAD)`, `med` и `MAD` — по `x(t′)` на слотах
//!    `t′ ∈ (t − w − H, t − w]` (`x(t′)` — тот же признак того же окна `w`, посчитанный в `t′`).
//! 4. **OI-признаки** берут слот `u = s − oi_lag_slots`; прирост за окно `w` — между `u` и `u − w/5`.
//! 5. **F1:** `x = OI[u]/OI[u−k] − 1` (доля к OI в начале окна; множитель 100 на z не влияет); `OI[u−k] ≤ 0` — `NaN`.
//!    **F2:** `x = OIV[u] − OIV[u−k]` в долларах (абсолютная разность `open_interest_value`).
//! 6. **F3, F4:** `x` — сумма `qvol` (оборот в $) / `trades` за минуты `(t − w, t]`; минута без свечи даёт 0 (так
//!    в кэше). Деления «на норму» нет: «к норме» = z.
//! 7. **F5–F8:** `x` — уровень колонки на слоте `u` как есть (без `ln`, без сглаживания по окну); окно `w` меняет
//!    лишь конец окна нормы (`t − w`), поэтому z четырёх окон близки.
//! 8. **F9:** `x = σ(w)/σ_H(t − w)` — `vr` из П-34: `σ(w)` — ст. откл. (по 1/N) `w` минутных ln-доходностей
//!    `close_ff` за `(t − w, t]`, `σ_H` — то же за `history_days` суток, кончающихся в `t − w`; `σ_H ≤ 0` — `NaN`;
//!    поверх `x` тот же робастный z (двухступенчатая нормировка — первый z через `2·history_days` суток).
//! 9. **F10:** `x` — последняя ставка с минутой выплаты `≤ t` (ставка действует с момента начисления; до первой
//!    выплаты — `NaN`); z к `funding_z_history_days` суток (история — та же 5-минутная выборка кусочно-постоянной
//!    ставки). Отдельного признака «изменение ставки» нет: ставка меняется раз в 1–8 ч, поэтому «изменение за
//!    `w ≤ 60` мин» нулевое в > 50 % истории (MAD = 0); изменение входит через сам z уровня (при выплате он скачет).
//! 10. **MAD = 0** (больше половины истории равны медиане — типично для ставки на базовом уровне): z по IEEE —
//!     `x = med` → 0, иначе `±∞` (правило срабатывает при любом θ); `NaN` в z — только когда нет данных.
//! 11. **`sigma`:** ст. откл. (по 1/N, без поправки) минутных ln-доходностей `close_ff` за `sigma_1m_window_days`
//!     суток до `t` включительно; окно должно быть полным, иначе `NaN` (до первого close доходности не определены).
//! 12. **`r`:** `ln(c[t]/c[t−w])` по `close_ff`; `NaN`, если `t − w` раньше первой свечи или close ещё не было.
//! 13. **Правила.** Семейства и знаковость признаков заданы здесь (в `Params` их нет): одиночные F1,F3,F4,F5,F6,F7,F8,
//!     F9,F10 (`fam` 0..=8), `S_oi` (9), `S_all` (10); вверх — F1–F4, F9, в обе стороны — F5–F8, F10. Составное
//!     правило — все компоненты ≥ θ в одном окне. Первое срабатывание: в `[t − L_pre, t)` срабатываний нет (как окно
//!     (а) §5); слоты до начала куба считаются несработавшими.
//!
//! # Алгоритм (В-170)
//!
//! Узкое место — скользящие медиана и MAD за 7 суток (2016 точек) × 25 рядов (F1–F4, F9 — на каждое из 4 окон; F5–F8
//! и F10 не зависят от окна) × ~2·10⁵ слотов × 642 контракта. Рассмотрено:
//! * наивно (сортировка окна на каждом слоте): `O(W log W)` ≈ 2·10⁴ операций на ряд-слот — ~10¹⁰ на контракт, не годится
//!   (эталон `feat_ref` — только для сверки);
//! * дерево Фенвика по рангам (сжатие координат): вставка/удаление/медиана `O(log W)`, но MAD (k-я по счёту из
//!   `|x − med|`) требует бинарного поиска по значению с подсчётами `O(log² W)` с большой константой;
//! * две кучи: медиана да, MAD нет;
//! * **выбрано:** отсортированное окно, замена «старое → новое» одним `copy_within` только между двумя позициями
//!   (в среднем ≈ W/3 элементов ≈ 5 КБ, оценка ≈ 0,3 мкс; без аллокаций после прогрева), MAD — как k-я по счёту из
//!   двух отсортированных последовательностей (расстояния от медианы вниз и вверх) бинарным поиском `O(log W)`.
//!   Точно (то же мультимножество и те же операции, что у эталона) — результат совпадает с эталоном до бита.
//! * z четырёх окон F5–F8, F10 берутся из одной пары рядов (med, MAD) со сдвигом — в 4 раза меньше работы;
//! * σ за 7 суток — суммы блоков по слоту (5 минут) в окне блоков через куски длины окна «префикс + суффикс»:
//!   только сложения, без вычитаний (дрейфа и катастрофического сокращения нет), `O(1)` на слот.
//!
//! Замер скорости на VPS не выполнялся автором модуля (cargo локально запрещён, В-147).

use super::cache::{Contract, SLOT_MIN};
use super::params::Params;
use super::types::{
    Cube, Rule, F_FUND, F_GLOBAL, F_OI_PCT, F_OI_USD, F_TAKER, F_TOP_COUNT, F_TOP_SUM, F_TRADES,
    F_TURN, F_VR, NF,
};

/// Минут в сутках (единица измерения, не параметр).
const DAY_MIN: i64 = 24 * 60;

/// Состав признаков 11 семейств П-34 §6: 0..=8 — одиночные F1,F3,F4,F5,F6,F7,F8,F9,F10 (без F2), 9 = S_oi, 10 = S_all.
pub const FAMILIES: [&[usize]; 11] = [
    &[F_OI_PCT],
    &[F_TURN],
    &[F_TRADES],
    &[F_TAKER],
    &[F_TOP_COUNT],
    &[F_TOP_SUM],
    &[F_GLOBAL],
    &[F_VR],
    &[F_FUND],
    &[F_OI_PCT, F_TURN],
    &[F_OI_PCT, F_TURN, F_TRADES],
];
/// Имена семейств в порядке `FAMILIES`.
pub const FAMILY_NAMES: [&str; 11] = [
    "F1", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "S_oi", "S_all",
];
/// Сигнал только вверх (`z ≥ θ`): F1–F4, F9; у остальных (F5–F8, F10) — `|z| ≥ θ`.
const UP_ONLY: [bool; NF] = [
    true, true, true, true, false, false, false, false, true, false,
];
/// Колонка `OI_COLS` признака: F1 — `open_interest`, F2 — `open_interest_value`, F5 — taker, F6/F7 — top-trader
/// count/sum, F8 — global count.
const OI_COL: [Option<usize>; NF] = [
    Some(0),
    Some(1),
    None,
    None,
    Some(5),
    Some(2),
    Some(3),
    Some(4),
    None,
    None,
];

/// Область куба: первый слот и число слотов (слот в области, если его минута `5·s` лежит в диапазоне свечей).
fn slot_range(c: &Contract) -> (i64, usize) {
    let slot0 = (c.first_min + SLOT_MIN - 1).div_euclid(SLOT_MIN);
    if c.close.is_empty() {
        return (slot0, 0);
    }
    let last = c.first_min + c.close.len() as i64 - 1;
    let slot1 = last.div_euclid(SLOT_MIN);
    if slot1 < slot0 {
        (slot0, 0)
    } else {
        (slot0, (slot1 - slot0 + 1) as usize)
    }
}

fn finite(v: f64) -> f64 {
    if v.is_finite() {
        v
    } else {
        f64::NAN
    }
}

/// Минутные ln-доходности по индексу массива; `NaN` — не определена (до первого close).
fn minute_returns(cf: &[f64]) -> Vec<f64> {
    let mut r = vec![f64::NAN; cf.len()];
    for (j, w) in cf.windows(2).enumerate() {
        r[j + 1] = finite((w[1] / w[0]).ln());
    }
    r
}

/// Суммы `ret` и `ret²` по блокам слотов: блок `i` — минуты `(5(s−1), 5s]`, `s = slot0 + i`. `NaN` → 0 (окна с
/// недоопределёнными доходностями отсекаются по первой определённой минуте).
fn block_sums(ret: &[f64], first_min: i64, slot0: i64, n: usize) -> (Vec<f64>, Vec<f64>) {
    let mut s1 = vec![0.0; n];
    let mut s2 = vec![0.0; n];
    for (i, (a, b)) in s1.iter_mut().zip(s2.iter_mut()).enumerate() {
        let t = (slot0 + i as i64) * SLOT_MIN;
        for m in t - SLOT_MIN + 1..=t {
            let j = m - first_min;
            if j >= 0 {
                let v = ret[j as usize];
                if !v.is_nan() {
                    *a += v;
                    *b += v * v;
                }
            }
        }
    }
    (s1, s2)
}

/// Скользящие суммы `b[j−w+1..=j]` (`NaN`, пока `j + 1 < w`) только сложениями: ряд режется на куски длины `w`;
/// окно = суффикс предыдущего куска + префикс текущего (или целый кусок). Значение в `j` зависит лишь от `b[..=j]`.
pub(crate) fn window_sums(b: &[f64], w: usize) -> Vec<f64> {
    let n = b.len();
    let mut out = vec![f64::NAN; n];
    if w == 0 || n < w {
        return out;
    }
    let mut pre = vec![0.0; n];
    for (j, &x) in b.iter().enumerate() {
        pre[j] = if j.is_multiple_of(w) {
            x
        } else {
            pre[j - 1] + x
        };
    }
    let mut suf = vec![0.0; n];
    for j in (0..n).rev() {
        suf[j] = if j + 1 == n || (j + 1).is_multiple_of(w) {
            b[j]
        } else {
            suf[j + 1] + b[j]
        };
    }
    for (j, o) in out.iter_mut().enumerate().skip(w - 1) {
        let a = j + 1 - w;
        *o = if a.is_multiple_of(w) {
            pre[j]
        } else {
            suf[a] + pre[j]
        };
    }
    out
}

/// Ст. откл. (по 1/N) минутных ln-доходностей за `wb` блоков (`wb·SLOT_MIN` минут), кончающихся блоком `i`.
/// `NaN`: окно неполное или начинается раньше первой определённой доходности (`fd_min` — её минута).
fn std_series(s1: &[f64], s2: &[f64], wb: usize, slot0: i64, fd_min: Option<i64>) -> Vec<f64> {
    let (w1, w2) = (window_sums(s1, wb), window_sums(s2, wb));
    let nmin = (wb as i64 * SLOT_MIN) as f64;
    w1.iter()
        .zip(&w2)
        .enumerate()
        .map(|(i, (&a, &b))| {
            let start = (slot0 + i as i64 - wb as i64) * SLOT_MIN + 1;
            if a.is_nan() || fd_min.is_none_or(|f| start < f) {
                return f64::NAN;
            }
            let mean = a / nmin;
            (b / nmin - mean * mean).max(0.0).sqrt()
        })
        .collect()
}

/// Ст. откл. (по 1/N) `ret[lo..=hi]` двумя проходами (окна `w ≤ 60` минут — точность важнее скорости).
fn std_direct(ret: &[f64], lo: usize, hi: usize) -> f64 {
    let part = &ret[lo..=hi];
    let n = part.len() as f64;
    let mut s = 0.0;
    for &v in part {
        s += v;
    }
    let mean = s / n;
    let mut q = 0.0;
    for &v in part {
        let d = v - mean;
        q += d * d;
    }
    (q / n).sqrt()
}

/// Контекст построения: ряды контракта, общие для всех признаков.
struct Ctx<'a> {
    c: &'a Contract,
    cf: Vec<f64>,
    ret: Vec<f64>,
    /// Индекс первой определённой доходности в `ret` (`ret.len()` — её нет).
    fd: usize,
    slot0: i64,
    lag: i64,
    /// Выплаты фандинга, по возрастанию минуты.
    fund: Vec<(i64, f64)>,
    /// `σ_H` по слотам (знаменатель F9).
    sig_hist: Vec<f64>,
}

impl Ctx<'_> {
    fn minute(&self, i: usize) -> i64 {
        (self.slot0 + i as i64) * SLOT_MIN
    }

    fn close_at(&self, m: i64) -> f64 {
        let j = m - self.c.first_min;
        if j < 0 {
            return f64::NAN;
        }
        self.cf.get(j as usize).copied().unwrap_or(f64::NAN)
    }

    fn oi_at(&self, col: usize, u: i64) -> f64 {
        let j = u - self.c.oi_first_slot;
        if j < 0 {
            return f64::NAN;
        }
        self.c.oi[col].get(j as usize).copied().unwrap_or(f64::NAN)
    }

    /// F1 (`relative`) и F2: прирост колонки OI между слотами `u` и `u − k`.
    fn fill_oi(&self, col: usize, k: i64, relative: bool, x: &mut [f64]) {
        for (i, xi) in x.iter_mut().enumerate() {
            let u = self.slot0 + i as i64 - self.lag;
            let (a, b) = (self.oi_at(col, u), self.oi_at(col, u - k));
            *xi = if !relative {
                finite(a - b)
            } else if b > 0.0 {
                finite((a - b) / b)
            } else {
                f64::NAN
            };
        }
    }

    /// F3, F4: сумма колонки за минуты `(t − w, t]`, окно целиком в ряду.
    fn fill_sum(&self, col: &[f32], w: i64, x: &mut [f64]) {
        for (i, xi) in x.iter_mut().enumerate() {
            let hi = self.minute(i) - self.c.first_min;
            let lo = hi - w + 1;
            let part = if lo >= 0 {
                col.get(lo as usize..=hi as usize)
            } else {
                None
            };
            *xi = match part {
                Some(part) => {
                    let mut s = 0.0;
                    for &v in part {
                        s += f64::from(v);
                    }
                    s
                }
                None => f64::NAN,
            };
        }
    }

    /// F9: `σ(w)/σ_H(t − w)`.
    fn fill_vr(&self, w: i64, k: i64, x: &mut [f64]) {
        for (i, xi) in x.iter_mut().enumerate() {
            let hi = self.minute(i) - self.c.first_min;
            let lo = hi - w + 1;
            let sh = if i as i64 >= k {
                self.sig_hist[i - k as usize]
            } else {
                f64::NAN
            };
            *xi = if lo >= 0 && lo as usize >= self.fd && sh > 0.0 {
                finite(std_direct(&self.ret, lo as usize, hi as usize) / sh)
            } else {
                f64::NAN
            };
        }
    }

    /// F10: последняя ставка с минутой выплаты `≤ t`.
    fn fill_fund(&self, x: &mut [f64]) {
        let (mut p, mut last) = (0, f64::NAN);
        for (i, xi) in x.iter_mut().enumerate() {
            let t = self.minute(i);
            while p < self.fund.len() && self.fund[p].0 <= t {
                last = self.fund[p].1;
                p += 1;
            }
            *xi = finite(last);
        }
    }

    /// Признаки, не зависящие от окна: F5–F8 (уровень на слоте `u`) и F10.
    fn fill_level(&self, f: usize, x: &mut [f64]) {
        match OI_COL[f] {
            Some(col) => {
                for (i, xi) in x.iter_mut().enumerate() {
                    *xi = finite(self.oi_at(col, self.slot0 + i as i64 - self.lag));
                }
            }
            None => self.fill_fund(x),
        }
    }

    /// Признаки, зависящие от окна `w`: F1–F4, F9.
    fn fill_window(&self, f: usize, w: i64, x: &mut [f64]) {
        let k = w / SLOT_MIN;
        match f {
            F_OI_PCT => self.fill_oi(0, k, true, x),
            F_OI_USD => self.fill_oi(1, k, false, x),
            F_TURN => self.fill_sum(&self.c.qvol, w, x),
            F_TRADES => self.fill_sum(&self.c.trades, w, x),
            F_VR => self.fill_vr(w, k, x),
            _ => unreachable!("уровневые признаки идут через fill_level"),
        }
    }
}

/// Вставка в отсортированное окно (`NaN` в окно не попадают).
fn win_insert(s: &mut Vec<f64>, v: f64) {
    let p = s.partition_point(|&a| a < v);
    s.insert(p, v);
}

fn win_remove(s: &mut Vec<f64>, v: f64) {
    let r = s.partition_point(|&a| a < v);
    s.remove(r);
}

/// Замена `old → new` в отсортированном окне: сдвигается только отрезок между двумя позициями.
fn win_replace(s: &mut [f64], old: f64, new: f64) {
    let r = s.partition_point(|&a| a < old);
    let p = s.partition_point(|&a| a < new);
    if p <= r {
        s.copy_within(p..r, p + 1);
        s[p] = new;
    } else {
        s.copy_within(r + 1..p, r);
        s[p - 1] = new;
    }
}

/// `k`-е (с нуля) по возрастанию значение объединения двух отсортированных последовательностей `a` (длина `na`) и
/// `b` (длина `nb`), `k < na + nb`; бинарный поиск числа взятых из `a`, `O(log)`.
fn kth_of_two(
    na: usize,
    nb: usize,
    a: &impl Fn(usize) -> f64,
    b: &impl Fn(usize) -> f64,
    k: usize,
) -> f64 {
    let (mut lo, mut hi) = ((k + 1).saturating_sub(nb), (k + 1).min(na));
    while lo < hi {
        let m = (lo + hi) / 2;
        if a(m) < b(k - m) {
            lo = m + 1;
        } else {
            hi = m;
        }
    }
    let x = if lo > 0 { a(lo - 1) } else { f64::NEG_INFINITY };
    let y = if k + 1 > lo {
        b(k - lo)
    } else {
        f64::NEG_INFINITY
    };
    x.max(y)
}

/// Медиана и MAD (медиана `|x − med|`) отсортированного непустого окна. Расстояния до значений `≤ med`
/// (вниз от медианы) и `> med` (вверх) уже отсортированы — MAD как `k`-я из двух последовательностей.
pub(crate) fn med_mad(s: &[f64]) -> (f64, f64) {
    let m = s.len();
    let h = m / 2;
    let med = if m % 2 == 1 {
        s[h]
    } else {
        (s[h - 1] + s[h]) / 2.0
    };
    let p = s.partition_point(|&v| v <= med);
    let down = |i: usize| med - s[p - 1 - i];
    let up = |i: usize| s[p + i] - med;
    let mad = if m % 2 == 1 {
        kth_of_two(p, m - p, &down, &up, h)
    } else {
        let lo = kth_of_two(p, m - p, &down, &up, h - 1);
        let hi = kth_of_two(p, m - p, &down, &up, h);
        (lo + hi) / 2.0
    };
    (med, mad)
}

/// Скользящие медиана и MAD по окну из `len` последних значений `x[j+1−len..=j]` (`NaN` пропускаются):
/// `med[j]`, `mad[j]`; `NaN`, пока окно не лежит целиком в данных ряда (начинается раньше первого определённого
/// `x`, `j + 1 < v0 + len`) или в нём нет значений. `x` — конечные числа или `NaN`; `med`, `mad` той же длины.
/// Аллокация одна — окно ёмкостью `len`.
pub(crate) fn rolling_med_mad(x: &[f64], len: usize, med: &mut [f64], mad: &mut [f64]) {
    let v0 = x.iter().position(|v| !v.is_nan());
    let mut s: Vec<f64> = Vec::with_capacity(len);
    for (j, &new) in x.iter().enumerate() {
        let old = if j >= len { x[j - len] } else { f64::NAN };
        match (old.is_nan(), new.is_nan()) {
            (true, true) => {}
            (true, false) => win_insert(&mut s, new),
            (false, true) => win_remove(&mut s, old),
            (false, false) => win_replace(&mut s, old, new),
        }
        if v0.is_some_and(|v| j + 1 >= v + len) && !s.is_empty() {
            (med[j], mad[j]) = med_mad(&s);
        } else {
            (med[j], mad[j]) = (f64::NAN, f64::NAN);
        }
    }
}

/// Робастный z; `MAD = 0` — по IEEE: `x = med` → 0, иначе `±∞` (допущение 10).
pub(crate) fn zval(x: f64, med: f64, mad: f64, factor: f64) -> f64 {
    if x.is_nan() || med.is_nan() || mad.is_nan() {
        return f64::NAN;
    }
    let dev = x - med;
    let den = factor * mad;
    if den > 0.0 {
        dev / den
    } else if dev == 0.0 {
        0.0
    } else {
        f64::INFINITY.copysign(dev)
    }
}

/// `z[i] = zval(x[i], med[i−k], mad[i−k])`: норма берётся по окну, кончающемуся в `t − w` (`k = w/5` слотов).
fn fill_z(z: &mut [f32], x: &[f64], med: &[f64], mad: &[f64], k: usize, factor: f64) {
    for (i, zi) in z.iter_mut().enumerate() {
        *zi = if i < k {
            f32::NAN
        } else {
            zval(x[i], med[i - k], mad[i - k], factor) as f32
        };
    }
}

/// Куб z / r / sigma контракта (П-34 §3). `oi_lag_slots`: 0 — метка `t_ms` OI = конец периода, 1 — консервативно
/// (шаг 0 протокола). Допущения и алгоритм — шапка модуля. Паника — только при `grid_min.value ≠ SLOT_MIN` или окнах,
/// не кратных слоту (ошибка конфигурации).
pub fn build_cube(c: &Contract, p: &Params, oi_lag_slots: u32) -> Cube {
    let ws = &p.features.windows_min;
    let nw = ws.len();
    assert!(
        i64::from(p.grid_min.value) == SLOT_MIN && ws.iter().all(|&w| i64::from(w) % SLOT_MIN == 0),
        "feat: grid_min.value должен равняться шагу кэша, окна — быть кратны ему"
    );
    let (slot0, n) = slot_range(c);
    let mut cube = Cube {
        slot0,
        n,
        windows: ws.clone(),
        z: vec![f32::NAN; NF * nw * n],
        r: vec![f32::NAN; nw * n],
        sigma: vec![f32::NAN; n],
    };
    if n == 0 {
        return cube;
    }
    let cf = c.close_ff();
    let ret = minute_returns(&cf);
    let fd = ret.iter().position(|v| !v.is_nan()).unwrap_or(ret.len());
    let fd_min = if fd < ret.len() {
        Some(c.first_min + fd as i64)
    } else {
        None
    };
    let (b1, b2) = block_sums(&ret, c.first_min, slot0, n);
    let per_day = (DAY_MIN / SLOT_MIN) as usize;
    let days = |d: u32| d as usize * per_day;
    let hist = days(p.features.norm.history_days);
    let sig_hist = std_series(&b1, &b2, hist, slot0, fd_min);
    let sig_reg = if p.controls.sigma_1m_window_days == p.features.norm.history_days {
        sig_hist.clone()
    } else {
        std_series(
            &b1,
            &b2,
            days(p.controls.sigma_1m_window_days),
            slot0,
            fd_min,
        )
    };
    for (s, &v) in cube.sigma.iter_mut().zip(&sig_reg) {
        *s = v as f32;
    }
    let mut fund = c.funding.clone();
    fund.sort_by_key(|&(m, _)| m);
    let ctx = Ctx {
        c,
        cf,
        ret,
        fd,
        slot0,
        lag: i64::from(oi_lag_slots),
        fund,
        sig_hist,
    };
    for (wi, &w) in ws.iter().enumerate() {
        for (i, ri) in cube.r[wi * n..(wi + 1) * n].iter_mut().enumerate() {
            let t = ctx.minute(i);
            *ri = (ctx.close_at(t) / ctx.close_at(t - i64::from(w))).ln() as f32;
        }
    }
    let mut x = vec![f64::NAN; n];
    let mut med = vec![f64::NAN; n];
    let mut mad = vec![f64::NAN; n];
    let factor = p.features.norm.mad_factor;
    for f in 0..NF {
        let level = matches!(f, F_TAKER | F_TOP_COUNT | F_TOP_SUM | F_GLOBAL | F_FUND);
        let len = if f == F_FUND {
            days(p.features.funding_z_history_days)
        } else {
            hist
        };
        if level {
            ctx.fill_level(f, &mut x);
            rolling_med_mad(&x, len, &mut med, &mut mad);
        }
        for (wi, &w) in ws.iter().enumerate() {
            if !level {
                ctx.fill_window(f, i64::from(w), &mut x);
                rolling_med_mad(&x, len, &mut med, &mut mad);
            }
            let o = (f * nw + wi) * n;
            let k = (i64::from(w) / SLOT_MIN) as usize;
            fill_z(&mut cube.z[o..o + n], &x, &med, &mad, k, factor);
        }
    }
    cube
}

/// Все правила П-34 §6: 11 семейств × окна × пороги, порядок `fam`, `wi`, `ti`.
pub fn rules(p: &Params) -> Vec<Rule> {
    let (nw, nt) = (p.features.windows_min.len(), p.features.theta.len());
    let mut v = Vec::with_capacity(FAMILIES.len() * nw * nt);
    for fam in 0..FAMILIES.len() {
        for wi in 0..nw {
            for ti in 0..nt {
                v.push(Rule {
                    fam: fam as u8,
                    wi: wi as u8,
                    ti: ti as u8,
                });
            }
        }
    }
    v
}

/// Срабатывание правила в слоте `i` куба (слот `cube.slot0 + i`): F1–F4, F9 — `z ≥ θ`; F5–F8, F10 — `|z| ≥ θ`;
/// составное — все компоненты в одном окне. Любой `NaN` (и `i` вне куба) — `false`.
pub fn fires(cube: &Cube, rule: Rule, p: &Params, i: usize) -> bool {
    if i >= cube.n {
        return false;
    }
    let th = p.features.theta[rule.ti as usize];
    FAMILIES[rule.fam as usize].iter().all(|&f| {
        let z = f64::from(cube.zat(f, rule.wi as usize, i));
        if UP_ONLY[f] {
            z >= th
        } else {
            z.abs() >= th
        }
    })
}

/// Индексы слотов куба с первыми срабатываниями правила: в предыдущие `L_pre` минут (`onset.L_pre_min/5` слотов,
/// `[t − L_pre, t)`) срабатываний этого правила не было; слоты с `NaN` считаются несработавшими.
pub fn first_triggers(cube: &Cube, rule: Rule, p: &Params) -> Vec<usize> {
    let quiet = (i64::from(p.onset.l_pre_min) / SLOT_MIN) as usize;
    let mut out = Vec::new();
    let mut last: Option<usize> = None;
    for i in 0..cube.n {
        if !fires(cube, rule, p, i) {
            continue;
        }
        if last.is_none_or(|l| l + quiet < i) {
            out.push(i);
        }
        last = Some(i);
    }
    out
}
