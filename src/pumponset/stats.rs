//! Статистика вердикта П-34 (TK-492): кластерный бутстрэп по суткам, поправка Холма, квантили.
//! Чистая математика: от остальных модулей — только генератор `SplitMix64` из `control`.
//!
//! # Бутстрэп по суткам (П-34 §5, Н-7)
//! Кластер = сутки, все монеты вместе. В реплике `r` один набор мультиномиальных весов суток `w_d`
//! (`D` розыгрышей из `D` суток с возвратом) общий для ВСЕХ ячеек; статистика ячейки — функция сумм
//! `Σ_d w_d · x_d` по суткам, где `x_d` — суточные суммы ячейки (разреженные: только сутки с данными).
//! Так реплика стоит `O(суток с данными)` на ячейку, а не `O(строк)`.
//!
//! Детерминизм: веса реплики `r` берутся из потока `stream(seed, r)` — зависят только от (зерно, номер
//! реплики, число суток), а не от потока ОС; потоки делят между собой ЯЧЕЙКИ (каждая ячейка считается одним
//! потоком в одном и том же порядке записей), поэтому результат побайтно не зависит от числа потоков.
//!
//! # Допущения
//! 1. Реплика, где статистика не определена (`NaN`: нет срабатываний/базы), из границ интервала исключается
//!    (число действительных — `Interval::valid`), а в `p` считается «не отвергла нуль» (консервативно).
//! 2. Квантиль — `floor(q·(n−1))` по отсортированным значениям (П-34/спека); прибавка `FLOOR_EPS` гасит ошибку
//!    двоичного представления `q` (0,975·40 = 38,999…), статистического смысла не несёт.
//! 3. Холм считается на `m` гипотез: протокол задаёт `m` (60/20) независимо от числа реально проверенных
//!    ячеек; недостающие `m − n` p-значения считаются равными 1 (консервативно).

#![allow(
    // численное ядро: индексы ограничены проверенными длинами; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::control::stream;
use anyhow::{bail, Result};

/// Допуск округления при переводе доли в индекс квантиля (см. допущение 2).
const FLOOR_EPS: f64 = 1e-9;

/// Квантиль `q` по методу нижнего ранга: `sorted[floor(q·(n−1))]`. `sorted` — по возрастанию, без `NaN`.
/// Пусто → `NaN`; `q` вне `[0,1]` обрезается краем.
pub fn quantile_floor(sorted: &[f64], q: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let last = sorted.len() - 1;
    let pos = (q * last as f64 + FLOOR_EPS).floor();
    let i = if pos <= 0.0 {
        0
    } else {
        (pos as usize).min(last)
    };
    sorted[i]
}

/// Разреженные суточные суммы многих ячеек: ячейка — список записей `(индекс суток, K чисел)`.
/// Записи ячейки идут подряд; порядок записей фиксирует порядок сложения (детерминизм).
#[derive(Debug, Clone)]
pub struct Sparse<const K: usize> {
    start: Vec<usize>,
    day: Vec<u32>,
    val: Vec<[f64; K]>,
}

impl<const K: usize> Default for Sparse<K> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const K: usize> Sparse<K> {
    pub fn new() -> Self {
        Self {
            start: vec![0],
            day: Vec::new(),
            val: Vec::new(),
        }
    }

    /// Запись в текущую (открытую) ячейку: сутки с индексом `day` (0-based, плотная нумерация), значения.
    pub fn push(&mut self, day: u32, v: [f64; K]) {
        self.day.push(day);
        self.val.push(v);
    }

    /// Закрывает текущую ячейку (пустая допустима).
    pub fn end_cell(&mut self) {
        self.start.push(self.day.len());
    }

    pub fn cells(&self) -> usize {
        self.start.len() - 1
    }

    /// Суммы ячейки без весов (точечная оценка).
    pub fn totals(&self, c: usize) -> [f64; K] {
        let mut s = [0.0; K];
        for v in &self.val[self.start[c]..self.start[c + 1]] {
            for (sj, vj) in s.iter_mut().zip(v.iter()) {
                *sj += *vj;
            }
        }
        s
    }

    /// Суммы ячейки с весами суток `w` (индекс суток → кратность).
    pub fn weighted(&self, c: usize, w: &[u32]) -> [f64; K] {
        let mut s = [0.0; K];
        let (a, b) = (self.start[c], self.start[c + 1]);
        for (d, v) in self.day[a..b].iter().zip(&self.val[a..b]) {
            let wd = f64::from(w[*d as usize]);
            for (sj, vj) in s.iter_mut().zip(v.iter()) {
                *sj += wd * *vj;
            }
        }
        s
    }

    fn max_day(&self) -> Option<u32> {
        self.day.iter().copied().max()
    }
}

/// Веса суток реплики `r`: `w.len()` розыгрышей с возвратом из `w.len()` суток. Зависит от (зерно, `r`, длина).
pub fn draw_weights(seed: u64, r: usize, w: &mut [u32]) {
    w.fill(0);
    let n = w.len() as u64;
    let mut rng = stream(seed, r as u64);
    for _ in 0..w.len() {
        w[rng.below(n) as usize] += 1;
    }
}

/// Значения статистик по репликам: `get(ячейка, статистика)` — срез из `b` значений (реплика `r` — позиция `r`).
#[derive(Debug, Clone)]
pub struct Replicas {
    b: usize,
    nstat: usize,
    data: Vec<f64>,
}

impl Replicas {
    pub fn b(&self) -> usize {
        self.b
    }

    pub fn get(&self, cell: usize, stat: usize) -> &[f64] {
        let o = (cell * self.nstat + stat) * self.b;
        &self.data[o..o + self.b]
    }

    /// Все значения подряд (раскладка `[(ячейка·S + статистика)·B + реплика]`) — для проверки детерминизма.
    pub fn raw(&self) -> &[f64] {
        &self.data
    }
}

/// Кластерный бутстрэп по суткам: `b` реплик, зерно `seed`, `ndays` суток-кластеров, `threads` потоков.
/// `stat` получает взвешенные суммы ячейки (`K` чисел) и пишет `S` статистик (`NaN` — не определена).
/// Индексы суток записей должны быть `< ndays`.
pub fn bootstrap<const K: usize, const S: usize, F>(
    sp: &Sparse<K>,
    ndays: usize,
    b: usize,
    seed: u64,
    threads: usize,
    stat: F,
) -> Result<Replicas>
where
    F: Fn(&[f64; K], &mut [f64; S]) + Sync,
{
    if let Some(d) = sp.max_day() {
        if d as usize >= ndays {
            bail!("бутстрэп: индекс суток {d} при {ndays} сутках");
        }
    }
    let nc = sp.cells();
    let mut data = vec![f64::NAN; nc * S * b];
    if nc == 0 || b == 0 || S == 0 {
        return Ok(Replicas { b, nstat: S, data });
    }
    let nt = threads.max(1).min(nc);
    let per = nc.div_ceil(nt);
    std::thread::scope(|sc| {
        for (ti, chunk) in data.chunks_mut(per * S * b).enumerate() {
            let c0 = ti * per;
            let stat = &stat;
            sc.spawn(move || {
                let ncl = chunk.len() / (S * b);
                let mut w = vec![0u32; ndays];
                for r in 0..b {
                    draw_weights(seed, r, &mut w);
                    for cl in 0..ncl {
                        let sums = sp.weighted(c0 + cl, &w);
                        let mut out = [f64::NAN; S];
                        stat(&sums, &mut out);
                        for (s, v) in out.iter().enumerate() {
                            chunk[(cl * S + s) * b + r] = *v;
                        }
                    }
                }
            });
        }
    });
    Ok(Replicas { b, nstat: S, data })
}

/// Интервал и p по репликам одной статистики (см. допущение 1 в шапке).
#[derive(Debug, Clone, Copy)]
pub struct Interval {
    /// Нижняя граница: квантиль `(1 − level)/2` действительных реплик.
    pub lo: f64,
    /// Верхняя граница: квантиль `1 − (1 − level)/2`.
    pub hi: f64,
    /// Односторонний p: `(1 + #реплик со стат ≤ null) / (B + 1)`; недействительные реплики входят в счёт.
    pub p: f64,
    /// Число действительных (не `NaN`) реплик.
    pub valid: usize,
}

pub fn interval(reps: &[f64], level: f64, null: f64) -> Interval {
    let mut v: Vec<f64> = reps.iter().copied().filter(|x| !x.is_nan()).collect();
    v.sort_by(f64::total_cmp);
    let le = v.iter().filter(|&&x| x <= null).count() + (reps.len() - v.len());
    let a = (1.0 - level) / 2.0;
    Interval {
        lo: quantile_floor(&v, a),
        hi: quantile_floor(&v, 1.0 - a),
        p: (1 + le) as f64 / (reps.len() + 1) as f64,
        valid: v.len(),
    }
}

/// Холм (step-down), скорректированные p в порядке входа. `m` — число гипотез протокола (допущение 3);
/// `m < p.len()` поднимается до `p.len()`. `NaN` в p считается 1. Ничья p — по порядку входа.
pub fn holm(p: &[f64], m: usize) -> Vec<f64> {
    let n = p.len();
    let m = m.max(n);
    let key = |x: f64| if x.is_nan() { 1.0 } else { x };
    let mut idx: Vec<usize> = (0..n).collect();
    idx.sort_by(|&a, &b| key(p[a]).total_cmp(&key(p[b])).then(a.cmp(&b)));
    let mut out = vec![1.0; n];
    let mut run = 0.0f64;
    for (j, &i) in idx.iter().enumerate() {
        let adj = ((m - j) as f64 * key(p[i])).min(1.0);
        run = run.max(adj);
        out[i] = run;
    }
    out
}
