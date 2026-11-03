//! Матричный предфильтр формирования (TK-486): корреляция доходностей по общим минутам для всех пар
//! вселенной за окно формирования — блоками, вместо цикла «пара × окно». Пропуски (`NaN`) не заполняются:
//! пара считается только по минутам, где значение есть у обеих ног (6 сумм по маске).
//!
//! Раскладка: отсчёты режутся на блоки по `CH`; в блоке у символа подряд лежат `x` (0 вместо `NaN`), маска
//! `m` и `q = x²` — один символ-блок влезает в L1, строки пар переиспользуют блок символа `j` для `RB`
//! строк сразу. Суммы блока — `f32` (8 дорожек), между блоками — `f64`.

use anyhow::{bail, Result};
use clap::Args;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

pub const CH: usize = 256;
/// Строк матрицы на задачу потока: блок символа `j` читается из кэша `RB` раз.
const RB: usize = 4;

type Tile<'a> = (&'a [f32; CH], &'a [f32; CH], &'a [f32; CH]);

/// Окно формирования: `s` символов × `t` отсчётов, раскладка блоками.
pub struct Packed {
    pub s: usize,
    pub t: usize,
    nch: usize,
    d: Vec<f32>,
}

impl Packed {
    /// `rows[s][k]` — доходность символа `s` в отсчёте `k`, `NaN` — нет значения; длины равны.
    pub fn new(rows: &[&[f64]]) -> Result<Packed> {
        let s = rows.len();
        let t = rows.first().map_or(0, |r| r.len());
        if rows.iter().any(|r| r.len() != t) {
            bail!("ряды разной длины");
        }
        let nch = t.div_ceil(CH);
        let mut d = vec![0f32; nch * s * 3 * CH];
        for (si, r) in rows.iter().enumerate() {
            for (k, &v) in r.iter().enumerate() {
                if v.is_finite() {
                    let base = (((k / CH) * s + si) * 3) * CH + k % CH;
                    let x = v as f32;
                    d[base] = x;
                    d[base + CH] = 1.0;
                    d[base + 2 * CH] = x * x;
                }
            }
        }
        Ok(Packed { s, t, nch, d })
    }

    #[inline]
    fn tile(&self, c: usize, s: usize) -> Tile<'_> {
        let o = ((c * self.s + s) * 3) * CH;
        let b = &self.d[o..o + 3 * CH];
        (
            <&[f32; CH]>::try_from(&b[..CH]).unwrap(),
            <&[f32; CH]>::try_from(&b[CH..2 * CH]).unwrap(),
            <&[f32; CH]>::try_from(&b[2 * CH..]).unwrap(),
        )
    }
}

/// Суммы блока для пары (a, b): `n, Σx_a·m_b, Σm_a·x_b, Σq_a·m_b, Σm_a·q_b, Σx_a·x_b`.
#[inline]
fn pair_chunk(a: Tile<'_>, b: Tile<'_>) -> [f32; 6] {
    let (xa, ma, qa) = a;
    let (xb, mb, qb) = b;
    let mut acc = [[0f32; 8]; 6];
    for k in (0..CH).step_by(8) {
        for l in 0..8 {
            let e = k + l;
            acc[0][l] += ma[e] * mb[e];
            acc[1][l] += xa[e] * mb[e];
            acc[2][l] += ma[e] * xb[e];
            acc[3][l] += qa[e] * mb[e];
            acc[4][l] += ma[e] * qb[e];
            acc[5][l] += xa[e] * xb[e];
        }
    }
    let mut r = [0f32; 6];
    for (o, a) in r.iter_mut().zip(acc.iter()) {
        *o = a.iter().sum();
    }
    r
}

/// Корреляция по суммам; `NaN` — общих отсчётов меньше `min_obs` или нулевая дисперсия.
fn finish(s: &[f64; 6], min_obs: u32) -> (f32, u32) {
    let n = s[0];
    if n < f64::from(min_obs.max(2)) {
        return (f32::NAN, n as u32);
    }
    let (sx, sy, sxx, syy, sxy) = (s[1], s[2], s[3], s[4], s[5]);
    let den = (n * sxx - sx * sx) * (n * syy - sy * sy);
    if den <= 0.0 {
        return (f32::NAN, n as u32);
    }
    (((n * sxy - sx * sy) / den.sqrt()) as f32, n as u32)
}

/// Матрицы корреляции и числа общих отсчётов, `s × s` построчно; заполнена верхняя часть (`i < j`).
pub struct Gram {
    pub s: usize,
    pub corr: Vec<f32>,
    pub n: Vec<u32>,
}

pub fn gram(p: &Packed, min_obs: u32, threads: usize) -> Gram {
    let s = p.s;
    let next = AtomicUsize::new(0);
    let out: Mutex<Vec<(usize, Vec<f32>, Vec<u32>)>> = Mutex::new(Vec::new());
    std::thread::scope(|sc| {
        for _ in 0..threads.max(1) {
            sc.spawn(|| loop {
                let i0 = next.fetch_add(1, Ordering::Relaxed) * RB;
                if i0 >= s {
                    break;
                }
                let rows = RB.min(s - i0);
                let mut acc = vec![[0f64; 6]; rows * s];
                for c in 0..p.nch {
                    for j in (i0 + 1)..s {
                        let b = p.tile(c, j);
                        for ii in 0..rows.min(j - i0) {
                            let r = pair_chunk(p.tile(c, i0 + ii), b);
                            let a = &mut acc[ii * s + j];
                            for (o, v) in a.iter_mut().zip(r) {
                                *o += f64::from(v);
                            }
                        }
                    }
                }
                let (mut cv, mut nv) = (vec![f32::NAN; rows * s], vec![0u32; rows * s]);
                for ii in 0..rows {
                    for j in (i0 + ii + 1)..s {
                        let (c, n) = finish(&acc[ii * s + j], min_obs);
                        cv[ii * s + j] = c;
                        nv[ii * s + j] = n;
                    }
                }
                out.lock().unwrap().push((i0, cv, nv));
            });
        }
    });
    let (mut corr, mut n) = (vec![f32::NAN; s * s], vec![0u32; s * s]);
    for (i0, cv, nv) in out.into_inner().unwrap() {
        let rows = cv.len() / s;
        corr[i0 * s..(i0 + rows) * s].copy_from_slice(&cv);
        n[i0 * s..(i0 + rows) * s].copy_from_slice(&nv);
    }
    Gram { s, corr, n }
}

/// Прямая корреляция одной пары по общим отсчётам (`f64`, один проход) — эталон для проверки.
pub fn naive_pair(a: &[f64], b: &[f64], min_obs: u32) -> (f32, u32) {
    let mut s = [0f64; 6];
    for (&x, &y) in a.iter().zip(b) {
        if x.is_finite() && y.is_finite() {
            s[0] += 1.0;
            s[1] += x;
            s[2] += y;
            s[3] += x * x;
            s[4] += y * y;
            s[5] += x * y;
        }
    }
    finish(&s, min_obs)
}

#[derive(Debug, Args)]
pub struct CorrBenchArgs {
    /// Символов во вселенной (синтетика: общий фактор + шум, 5 % пропусков).
    #[arg(long)]
    symbols: usize,
    /// Отсчётов в окне формирования (90 суток по 1 м = 129600).
    #[arg(long)]
    minutes: usize,
    #[arg(long)]
    min_obs: u32,
    #[arg(long)]
    threads: usize,
    /// Сколько пар сверить с прямым расчётом.
    #[arg(long)]
    check_pairs: usize,
}

/// Замер: время упаковки, время матрицы, пар-отсчётов в секунду, максимум |Δcorr| против прямого расчёта.
pub fn run(a: &CorrBenchArgs) -> Result<()> {
    let mut state = 0x9E37_79B9_7F4A_7C15u64;
    let mut u = move || {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((state >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    };
    let factor: Vec<f64> = (0..a.minutes).map(|_| u()).collect();
    let rows: Vec<Vec<f64>> = (0..a.symbols)
        .map(|s| {
            let w = (s % 7) as f64 / 7.0;
            (0..a.minutes)
                .map(|k| {
                    if u() > 0.9 {
                        f64::NAN
                    } else {
                        1e-3 * (w * factor[k] + (1.0 - w) * u())
                    }
                })
                .collect()
        })
        .collect();
    let refs: Vec<&[f64]> = rows.iter().map(|r| r.as_slice()).collect();
    let t0 = Instant::now();
    let p = Packed::new(&refs)?;
    let t_pack = t0.elapsed().as_secs_f64();
    let t1 = Instant::now();
    let g = gram(&p, a.min_obs, a.threads);
    let t_gram = t1.elapsed().as_secs_f64();
    let pairs = (a.symbols * a.symbols.saturating_sub(1) / 2) as f64;
    let (mut worst, mut checked) = (0f32, 0usize);
    let t2 = Instant::now();
    'chk: for i in 0..a.symbols {
        for j in (i + 1)..a.symbols {
            if checked >= a.check_pairs {
                break 'chk;
            }
            let (c, n) = naive_pair(&rows[i], &rows[j], a.min_obs);
            if n != g.n[i * a.symbols + j] {
                bail!("пара {i},{j}: n {} против {n}", g.n[i * a.symbols + j]);
            }
            let d = (c - g.corr[i * a.symbols + j]).abs();
            if d.is_finite() {
                worst = worst.max(d);
            }
            checked += 1;
        }
    }
    let t_naive = t2.elapsed().as_secs_f64();
    let naive_rate = checked as f64 * a.minutes as f64 / t_naive.max(1e-9);
    println!(
        "naive: пар {}, {:.2} с, {:.3e} пар·отсчётов/с (один поток, прямой расчёт)",
        checked, t_naive, naive_rate
    );
    println!(
        "corr: символов {}, отсчётов {}, пар {:.0}, потоков {}, упаковка {:.2} с, матрица {:.2} с, {:.3e} пар·отсчётов/с, сверено пар {}, max|Δcorr| {:.2e}",
        a.symbols,
        a.minutes,
        pairs,
        a.threads,
        t_pack,
        t_gram,
        pairs * a.minutes as f64 / t_gram,
        checked,
        worst
    );
    Ok(())
}
