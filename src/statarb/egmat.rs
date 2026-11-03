//! Суммы теста Энгла–Грейнджера для всех пар вселенной по блокам отсчётов (TK-486): 15 сумм `EgSums` на пару и блок
//! одним проходом, окно формирования и его половины (P-32 §6.1) — суммы подряд идущих блоков, без пересчёта «пара × окно».
//!
//! Суммы считаются в `f64`: тест строится на уровнях ln-цен, остаток мал по сравнению с ними, и `f32`-суммы
//! (как у корреляции доходностей в `corr.rs`) съели бы точность. Ряд центруется на символ до сумм (сдвиг не меняет
//! β, `t` и остаток, см. `eg.rs`). Отсчёт `k` в блоке — пара `(k−1, k)`: блок не разрывает лаг, сумма блоков
//! равна прямому проходу по ряду.
//!
//! Раскладка: отсчёты режутся на куски по `CH`; у символа в куске подряд лежат 6 векторов `m, x, x1, x², x1², x·x1`
//! (0 там, где нет значения в `k` или `k−1`), пара — 15 скалярных произведений этих векторов.

use super::corr::pair_idx;
use super::eg::EgSums;
use anyhow::{bail, Result};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

pub const CH: usize = 128;
/// Строк матрицы на задачу потока: блок символа `j` читается из кэша `RB` раз.
const RB: usize = 4;
const V: usize = 6;

/// Суммы блоков одной полосы строк: (первая строка, строк, суммы блок × строка × столбец).
type RowStrip = (usize, usize, Vec<EgSums>);

type Tile<'a> = [&'a [f64; CH]; V];

pub struct PackedEg {
    pub s: usize,
    pub t: usize,
    nch: usize,
    d: Vec<f64>,
}

impl PackedEg {
    /// `rows[s][k]` — ln-цена символа `s` в отсчёте `k`, `NaN` — нет свечи; длины равны.
    pub fn new(rows: &[&[f64]]) -> Result<PackedEg> {
        let s = rows.len();
        let t = rows.first().map_or(0, |r| r.len());
        if rows.iter().any(|r| r.len() != t) {
            bail!("ряды разной длины");
        }
        let nch = t.div_ceil(CH);
        let mut d = vec![0f64; nch * s * V * CH];
        for (si, r) in rows.iter().enumerate() {
            let fin: Vec<f64> = r.iter().copied().filter(|a| a.is_finite()).collect();
            let mean = if fin.is_empty() {
                0.0
            } else {
                fin.iter().sum::<f64>() / fin.len() as f64
            };
            for k in 1..t {
                if r[k].is_finite() && r[k - 1].is_finite() {
                    let (x, x1) = (r[k] - mean, r[k - 1] - mean);
                    let base = (((k / CH) * s + si) * V) * CH + k % CH;
                    for (v, val) in [1.0, x, x1, x * x, x1 * x1, x * x1].into_iter().enumerate() {
                        d[base + v * CH] = val;
                    }
                }
            }
        }
        Ok(PackedEg { s, t, nch, d })
    }

    #[inline]
    fn tile(&self, c: usize, s: usize) -> Tile<'_> {
        let o = ((c * self.s + s) * V) * CH;
        let b = &self.d[o..o + V * CH];
        std::array::from_fn(|v| <&[f64; CH]>::try_from(&b[v * CH..(v + 1) * CH]).unwrap())
    }
}

#[inline]
fn lanes(v: &[f64; CH]) -> &[[f64; 4]] {
    v.as_chunks::<4>().0
}

/// 15 сумм куска для пары (a, b): a — нога `x`, b — нога `y`. Порядок — как в `EgSums::add`.
#[inline]
fn pair_chunk(a: Tile<'_>, b: Tile<'_>) -> [f64; 15] {
    let [ma, xa, x1a, qa, q1a, pa] = a;
    let [mb, xb, x1b, qb, q1b, pb] = b;
    let mut acc = [[0f64; 4]; 15];
    let it = lanes(ma)
        .iter()
        .zip(lanes(xa))
        .zip(lanes(x1a))
        .zip(lanes(qa))
        .zip(lanes(q1a))
        .zip(lanes(pa))
        .zip(lanes(mb))
        .zip(lanes(xb))
        .zip(lanes(x1b))
        .zip(lanes(qb))
        .zip(lanes(q1b))
        .zip(lanes(pb));
    for (((((((((((ma, xa), x1a), qa), q1a), pa), mb), xb), x1b), qb), q1b), pb) in it {
        for l in 0..4 {
            acc[0][l] += ma[l] * mb[l];
            acc[1][l] += xa[l] * mb[l];
            acc[2][l] += x1a[l] * mb[l];
            acc[3][l] += ma[l] * xb[l];
            acc[4][l] += ma[l] * x1b[l];
            acc[5][l] += qa[l] * mb[l];
            acc[6][l] += q1a[l] * mb[l];
            acc[7][l] += pa[l] * mb[l];
            acc[8][l] += ma[l] * qb[l];
            acc[9][l] += ma[l] * q1b[l];
            acc[10][l] += ma[l] * pb[l];
            acc[11][l] += xa[l] * xb[l];
            acc[12][l] += x1a[l] * x1b[l];
            acc[13][l] += xa[l] * x1b[l];
            acc[14][l] += x1a[l] * xb[l];
        }
    }
    let mut r = [0f64; 15];
    for (o, a) in r.iter_mut().zip(acc.iter()) {
        *o = a.iter().sum();
    }
    r
}

#[inline]
fn add_chunk(e: &mut EgSums, r: &[f64; 15]) {
    e.n += r[0];
    e.x += r[1];
    e.x1 += r[2];
    e.y += r[3];
    e.y1 += r[4];
    e.xx += r[5];
    e.x1x1 += r[6];
    e.xx1 += r[7];
    e.yy += r[8];
    e.y1y1 += r[9];
    e.yy1 += r[10];
    e.xy += r[11];
    e.x1y1 += r[12];
    e.xy1 += r[13];
    e.x1y += r[14];
}

/// Суммы всех пар `i < j` по блокам из `block_chunks` кусков (`CH` отсчётов каждый); нога `x` = `i`, `y` = `j`.
pub struct EgBlocks {
    pub s: usize,
    pub blocks: usize,
    m: Vec<EgSums>,
}

pub fn eg_blocks(p: &PackedEg, block_chunks: usize, threads: usize) -> EgBlocks {
    let s = p.s;
    let bc = block_chunks.max(1);
    let blocks = p.nch.div_ceil(bc).max(1);
    let next = AtomicUsize::new(0);
    let out: Mutex<Vec<RowStrip>> = Mutex::new(Vec::new());
    std::thread::scope(|sc| {
        for _ in 0..threads.max(1) {
            sc.spawn(|| loop {
                let i0 = next.fetch_add(1, Ordering::Relaxed) * RB;
                if i0 >= s {
                    break;
                }
                let rows = RB.min(s - i0);
                let mut acc = vec![EgSums::default(); blocks * rows * s];
                for c in 0..p.nch {
                    let bl = c / bc;
                    for j in (i0 + 1)..s {
                        let b = p.tile(c, j);
                        for ii in 0..rows.min(j - i0) {
                            let r = pair_chunk(p.tile(c, i0 + ii), b);
                            add_chunk(&mut acc[(bl * rows + ii) * s + j], &r);
                        }
                    }
                }
                out.lock().unwrap().push((i0, rows, acc));
            });
        }
    });
    let np = s * s.saturating_sub(1) / 2;
    let mut m = vec![EgSums::default(); blocks * np];
    for (i0, rows, acc) in out.into_inner().unwrap() {
        for bl in 0..blocks {
            for ii in 0..rows {
                let i = i0 + ii;
                for j in (i + 1)..s {
                    m[bl * np + pair_idx(s, i, j)] = acc[(bl * rows + ii) * s + j];
                }
            }
        }
    }
    EgBlocks { s, blocks, m }
}

impl EgBlocks {
    /// Суммы пары `i < j` по блокам `lo..hi`.
    pub fn pair_window(&self, i: usize, j: usize, lo: usize, hi: usize) -> EgSums {
        let np = self.s * self.s.saturating_sub(1) / 2;
        let k = pair_idx(self.s, i, j);
        let mut t = EgSums::default();
        for b in lo..hi.min(self.blocks) {
            t.merge(&self.m[b * np + k]);
        }
        t
    }
}
