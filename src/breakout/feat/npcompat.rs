//! Побитовые копии операций numpy 2.2.4, на которых стоит `tools/p42/tk594_feat.py`.
//!
//! `np.sum` по непрерывному float64-массиву считает не последовательно: массив режется на куски по 8192 элемента
//! (буфер итератора), куски складываются в аккумулятор по порядку, внутри куска — попарное суммирование блоками по
//! 128 с восемью аккумуляторами (`DOUBLE_pairwise_sum`). Порядок подтверждён на srv: 1236 случаев длиной 0…10⁵ и
//! 27 длиной до 10⁶ (`tools/p42/np_golden.py`, золотой набор `golden_np.json`, тест `tests::golden_*`).
//! `np.cumsum` — последовательно; `np.mean` — `np.sum` / n.

/// Блок попарного суммирования numpy (`PW_BLOCKSIZE`).
const PW_BLOCK: usize = 128;
/// Размер куска редукции numpy (буфер итератора, элементов).
const REDUCE_CHUNK: usize = 8192;

fn pairwise(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = -0.0f64;
        for &x in a {
            res += x;
        }
        res
    } else if n <= PW_BLOCK {
        let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
        let lim = n - (n % 8);
        let mut i = 8;
        while i < lim {
            for (j, rj) in r.iter_mut().enumerate() {
                *rj += a[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise(&a[..n2]) + pairwise(&a[n2..])
    }
}

/// `float(np.sum(a))`, `a` — непрерывный срез float64.
pub fn np_sum(a: &[f64]) -> f64 {
    let mut acc = 0.0f64;
    for c in a.chunks(REDUCE_CHUNK) {
        acc += pairwise(c);
    }
    acc
}

/// `float(np.mean(a))` (`a` не пуст): `np.sum(a) / n`.
pub fn np_mean(a: &[f64]) -> f64 {
    np_sum(a) / a.len() as f64
}

/// `np.cumsum(a)`: `out[0] = a[0]`, `out[i] = out[i-1] + a[i]`.
pub fn np_cumsum(a: &[f64]) -> Vec<f64> {
    let mut out = Vec::with_capacity(a.len());
    let mut acc = 0.0f64;
    for (i, &x) in a.iter().enumerate() {
        acc = if i == 0 { x } else { acc + x };
        out.push(acc);
    }
    out
}

/// `np.searchsorted(a, x, "left")` для отсортированного `a` (без NaN).
pub fn ss_left_i64(a: &[i64], x: i64) -> usize {
    a.partition_point(|&v| v < x)
}

/// `np.searchsorted(a, x, "right")`.
pub fn ss_right_i64(a: &[i64], x: i64) -> usize {
    a.partition_point(|&v| v <= x)
}

pub fn ss_left_f64(a: &[f64], x: f64) -> usize {
    a.partition_point(|&v| v < x)
}

pub fn ss_right_f64(a: &[f64], x: f64) -> usize {
    a.partition_point(|&v| v <= x)
}
