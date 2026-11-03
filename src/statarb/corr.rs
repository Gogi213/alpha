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

/// Суммы блоков одной полосы строк матрицы: (первая строка, строк, суммы блок × строка × столбец).
type RowStrip = (usize, usize, Vec<[f64; 6]>);

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
// прежнее ядро (`Ref`) оставлено дословно: эталон порядка сложения для остальных ядер
#[allow(clippy::needless_range_loop)]
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

/// Вариант ядра суммирования блока (TK-486): `Ref` — прежний, `Zip` и `Zip2` — тот же порядок сложения (8 дорожек,
/// те же суммы), но без проверок границ в цикле (цикл векторизуется) и по 2 строки на один проход блока `j`;
/// `Avx` — явные 256-бит команды (раздельные умножение и сложение, без FMA: те же суммы бит в бит), на ЦП без AVX — `Zip`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Kernel {
    #[default]
    Ref,
    Zip,
    Zip2,
    Avx,
}

impl std::str::FromStr for Kernel {
    type Err = String;
    fn from_str(v: &str) -> Result<Self, String> {
        match v {
            "ref" => Ok(Kernel::Ref),
            "zip" => Ok(Kernel::Zip),
            "zip2" => Ok(Kernel::Zip2),
            "avx" => Ok(Kernel::Avx),
            _ => Err(format!("ядро: ref|zip|zip2|avx, получено {v}")),
        }
    }
}

type Lane = [f32; 8];

#[inline]
fn lanes(a: &[f32; CH]) -> &[Lane] {
    a.as_chunks::<8>().0
}

#[inline]
fn fold(acc: &[Lane; 6]) -> [f32; 6] {
    let mut r = [0f32; 6];
    for (o, a) in r.iter_mut().zip(acc.iter()) {
        *o = a.iter().sum();
    }
    r
}

/// То же, что `pair_chunk`, но без индексации по `e` (границы известны типом `[f32; 8]`).
#[inline]
fn pair_chunk_zip(a: Tile<'_>, b: Tile<'_>) -> [f32; 6] {
    let (xa, ma, qa) = a;
    let (xb, mb, qb) = b;
    let mut acc = [[0f32; 8]; 6];
    let it = lanes(xa)
        .iter()
        .zip(lanes(ma))
        .zip(lanes(qa))
        .zip(lanes(xb))
        .zip(lanes(mb))
        .zip(lanes(qb));
    for (((((xa, ma), qa), xb), mb), qb) in it {
        for l in 0..8 {
            acc[0][l] += ma[l] * mb[l];
            acc[1][l] += xa[l] * mb[l];
            acc[2][l] += ma[l] * xb[l];
            acc[3][l] += qa[l] * mb[l];
            acc[4][l] += ma[l] * qb[l];
            acc[5][l] += xa[l] * xb[l];
        }
    }
    fold(&acc)
}

/// Те же суммы и тот же порядок сложения по дорожкам, что у `pair_chunk`, но 256-бит командами: на 8 дорожек —
/// 6 умножений и 6 сложений (без FMA, чтобы округление совпало с `Ref`).
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx")]
unsafe fn pair_chunk_avx_impl(a: Tile<'_>, b: Tile<'_>) -> [f32; 6] {
    use std::arch::x86_64::{
        _mm256_add_ps, _mm256_loadu_ps, _mm256_mul_ps, _mm256_setzero_ps, _mm256_storeu_ps,
    };
    let (xa, ma, qa) = a;
    let (xb, mb, qb) = b;
    let mut acc = [_mm256_setzero_ps(); 6];
    for k in (0..CH).step_by(8) {
        let (vxa, vma, vqa) = (
            _mm256_loadu_ps(xa[k..].as_ptr()),
            _mm256_loadu_ps(ma[k..].as_ptr()),
            _mm256_loadu_ps(qa[k..].as_ptr()),
        );
        let (vxb, vmb, vqb) = (
            _mm256_loadu_ps(xb[k..].as_ptr()),
            _mm256_loadu_ps(mb[k..].as_ptr()),
            _mm256_loadu_ps(qb[k..].as_ptr()),
        );
        acc[0] = _mm256_add_ps(acc[0], _mm256_mul_ps(vma, vmb));
        acc[1] = _mm256_add_ps(acc[1], _mm256_mul_ps(vxa, vmb));
        acc[2] = _mm256_add_ps(acc[2], _mm256_mul_ps(vma, vxb));
        acc[3] = _mm256_add_ps(acc[3], _mm256_mul_ps(vqa, vmb));
        acc[4] = _mm256_add_ps(acc[4], _mm256_mul_ps(vma, vqb));
        acc[5] = _mm256_add_ps(acc[5], _mm256_mul_ps(vxa, vxb));
    }
    let mut lanes = [[0f32; 8]; 6];
    for (l, v) in lanes.iter_mut().zip(acc) {
        _mm256_storeu_ps(l.as_mut_ptr(), v);
    }
    fold(&lanes)
}

/// `Avx` без проверки процессора вызывать нельзя: вызывающий берёт `avx_ok()` один раз на проход.
#[cfg(target_arch = "x86_64")]
fn avx_ok() -> bool {
    std::arch::is_x86_feature_detected!("avx")
}
#[cfg(not(target_arch = "x86_64"))]
fn avx_ok() -> bool {
    false
}

#[inline]
fn pair_chunk_avx(a: Tile<'_>, b: Tile<'_>) -> [f32; 6] {
    #[cfg(target_arch = "x86_64")]
    {
        // SAFETY: вызывается только при `avx_ok()` (см. `block_moments_with`).
        unsafe { pair_chunk_avx_impl(a, b) }
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        pair_chunk_zip(a, b)
    }
}

/// Две строки (`a0`, `a1`) против одного блока `b`: блок `b` читается из кэша один раз на две пары.
#[inline]
fn pair_chunk_zip2(a0: Tile<'_>, a1: Tile<'_>, b: Tile<'_>) -> [[f32; 6]; 2] {
    let (xb, mb, qb) = b;
    let mut acc0 = [[0f32; 8]; 6];
    let mut acc1 = [[0f32; 8]; 6];
    let it = lanes(a0.0)
        .iter()
        .zip(lanes(a0.1))
        .zip(lanes(a0.2))
        .zip(lanes(a1.0))
        .zip(lanes(a1.1))
        .zip(lanes(a1.2))
        .zip(lanes(xb))
        .zip(lanes(mb))
        .zip(lanes(qb));
    for ((((((((xa, ma), qa), xc), mc), qc), xb), mb), qb) in it {
        for l in 0..8 {
            acc0[0][l] += ma[l] * mb[l];
            acc0[1][l] += xa[l] * mb[l];
            acc0[2][l] += ma[l] * xb[l];
            acc0[3][l] += qa[l] * mb[l];
            acc0[4][l] += ma[l] * qb[l];
            acc0[5][l] += xa[l] * xb[l];
            acc1[0][l] += mc[l] * mb[l];
            acc1[1][l] += xc[l] * mb[l];
            acc1[2][l] += mc[l] * xb[l];
            acc1[3][l] += qc[l] * mb[l];
            acc1[4][l] += mc[l] * qb[l];
            acc1[5][l] += xc[l] * xb[l];
        }
    }
    [fold(&acc0), fold(&acc1)]
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

/// Номер пары `i < j` в упакованном верхнем треугольнике `s × s`.
pub(crate) fn pair_idx(s: usize, i: usize, j: usize) -> usize {
    i * s - i * (i + 1) / 2 + (j - i - 1)
}

/// Суммы пар (6 штук, как в `pair_chunk`) по блокам из `block_chunks` кусков отсчётов: окно формирования —
/// сумма подряд идущих блоков, скользящее окно не пересчитывает общие блоки (TK-486).
pub struct BlockMoments {
    pub s: usize,
    pub blocks: usize,
    m: Vec<[f64; 6]>,
}

pub fn block_moments(p: &Packed, block_chunks: usize, threads: usize) -> BlockMoments {
    block_moments_with(p, block_chunks, threads, Kernel::Ref)
}

pub fn block_moments_with(
    p: &Packed,
    block_chunks: usize,
    threads: usize,
    kernel: Kernel,
) -> BlockMoments {
    let s = p.s;
    let kernel = if kernel == Kernel::Avx && !avx_ok() {
        Kernel::Zip
    } else {
        kernel
    };
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
                let mut acc = vec![[0f64; 6]; blocks * rows * s];
                for c in 0..p.nch {
                    let bl = c / bc;
                    for j in (i0 + 1)..s {
                        let b = p.tile(c, j);
                        let lim = rows.min(j - i0);
                        let mut ii = 0;
                        while ii < lim {
                            let two = kernel == Kernel::Zip2 && ii + 1 < lim;
                            let (r0, r1) = match kernel {
                                Kernel::Ref => (pair_chunk(p.tile(c, i0 + ii), b), None),
                                Kernel::Zip => (pair_chunk_zip(p.tile(c, i0 + ii), b), None),
                                Kernel::Avx => (pair_chunk_avx(p.tile(c, i0 + ii), b), None),
                                Kernel::Zip2 if two => {
                                    let [r0, r1] = pair_chunk_zip2(
                                        p.tile(c, i0 + ii),
                                        p.tile(c, i0 + ii + 1),
                                        b,
                                    );
                                    (r0, Some(r1))
                                }
                                Kernel::Zip2 => (pair_chunk_zip(p.tile(c, i0 + ii), b), None),
                            };
                            for (k, r) in [Some(r0), r1].into_iter().flatten().enumerate() {
                                let a = &mut acc[(bl * rows + ii + k) * s + j];
                                for (o, v) in a.iter_mut().zip(r) {
                                    *o += f64::from(v);
                                }
                            }
                            ii += if two { 2 } else { 1 };
                        }
                    }
                }
                out.lock().unwrap().push((i0, rows, acc));
            });
        }
    });
    let np = s * s.saturating_sub(1) / 2;
    let mut m = vec![[0f64; 6]; blocks * np];
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
    BlockMoments { s, blocks, m }
}

impl BlockMoments {
    /// Корреляция и число общих отсчётов по окну из блоков `lo..hi`.
    pub fn window(&self, lo: usize, hi: usize, min_obs: u32) -> Gram {
        let s = self.s;
        let np = s * s.saturating_sub(1) / 2;
        let (mut corr, mut n) = (vec![f32::NAN; s * s], vec![0u32; s * s]);
        for i in 0..s {
            for j in (i + 1)..s {
                let k = pair_idx(s, i, j);
                let mut t = [0f64; 6];
                for b in lo..hi.min(self.blocks) {
                    for (o, v) in t.iter_mut().zip(self.m[b * np + k]) {
                        *o += v;
                    }
                }
                let (c, nn) = finish(&t, min_obs);
                corr[i * s + j] = c;
                n[i * s + j] = nn;
            }
        }
        Gram { s, corr, n }
    }
}

/// Одно окно на весь ряд: один блок.
pub fn gram(p: &Packed, min_obs: u32, threads: usize) -> Gram {
    gram_with(p, min_obs, threads, Kernel::Ref)
}

pub fn gram_with(p: &Packed, min_obs: u32, threads: usize, kernel: Kernel) -> Gram {
    block_moments_with(p, p.nch.max(1), threads, kernel).window(0, 1, min_obs)
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
    /// Ядро суммирования блока: ref (прежнее, умолчание) | zip | zip2 | avx (TK-486).
    #[arg(long, default_value = "ref")]
    kernel: Kernel,
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
    let g = gram_with(&p, a.min_obs, a.threads, a.kernel);
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
        "corr[{:?}]: символов {}, отсчётов {}, пар {:.0}, потоков {}, упаковка {:.2} с, матрица {:.2} с, {:.3e} пар·отсчётов/с, сверено пар {}, max|Δcorr| {:.2e}",
        a.kernel,
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
