//! Наивный эталон эпизодов (TK-492): определение «в лоб», O(n·W) на `(D, знак)`, без очередей и гистограмм.
//! Служит только сверке `episodes::{find_episodes, XdAcc, mark_market}` (бит в бит); в прогон не входит.

use super::cache::Contract;
use super::episodes::{is_leverage, MIN_DUR};
use super::params::Params;
use super::types::{Episode, XdTable, FLAG_MARKET, FLAG_OUTLIER, FLAG_WICK};

/// Ход по концу `j`: вверх / вниз и индексы стартов (самый ранний при равенстве); `NaN`, если хода нет.
#[derive(Clone, Copy, Debug)]
pub struct NaiveMove {
    pub up: f64,
    pub iu: usize,
    pub dn: f64,
    pub id: usize,
}

/// `close_ff` и его `ln` — независимо от `Contract::close_ff`.
pub fn ff_ln(c: &Contract) -> (Vec<f64>, Vec<f64>) {
    let mut ff: Vec<f64> = Vec::with_capacity(c.close.len());
    for (k, &x) in c.close.iter().enumerate() {
        ff.push(if !x.is_nan() {
            x
        } else if k > 0 {
            ff[k - 1]
        } else {
            f64::NAN
        });
    }
    let lc = ff.iter().map(|x| x.ln()).collect();
    (ff, lc)
}

/// Ходы по определению: перебор окна старта `[j − w, j − MIN_DUR]` (обрезано началом ряда), `NaN`-минуты пропускаются.
pub fn moves_naive(lc: &[f64], w: usize) -> Vec<NaiveMove> {
    (0..lc.len())
        .map(|j| {
            let none = NaiveMove {
                up: f64::NAN,
                iu: 0,
                dn: f64::NAN,
                id: 0,
            };
            if j < MIN_DUR || lc[j].is_nan() {
                return none;
            }
            let (mut imin, mut imax) = (None::<usize>, None::<usize>);
            for s in j.saturating_sub(w)..=j - MIN_DUR {
                if lc[s].is_nan() {
                    continue;
                }
                if imin.is_none_or(|m| lc[s] < lc[m]) {
                    imin = Some(s);
                }
                if imax.is_none_or(|m| lc[s] > lc[m]) {
                    imax = Some(s);
                }
            }
            let (Some(im), Some(ix)) = (imin, imax) else {
                return none;
            };
            let (up, dn) = (lc[j] - lc[im], lc[ix] - lc[j]);
            NaiveMove {
                up: if up > 0.0 { up } else { 0.0 },
                iu: im,
                dn: if dn > 0.0 { dn } else { 0.0 },
                id: ix,
            }
        })
        .collect()
}

/// Эпизоды по определению (см. `episodes::find_episodes`): кандидаты `ход ≥ X`, жадно по убыванию хода,
/// пересечение — перебором всех принятых, E4 и хвост — прямым счётом.
pub fn find_episodes_naive(c: &Contract, p: &Params, xd: &XdTable, sym: &str) -> Vec<Episode> {
    let mut out = Vec::new();
    if is_leverage(sym) {
        return out;
    }
    let (ff, lc) = ff_ln(c);
    let n = ff.len();
    let e = &p.episodes;
    for (di, &d) in e.d_min.iter().enumerate() {
        let mv = moves_naive(&lc, d as usize);
        for (k, sign) in [(0usize, 1i8), (1, -1)] {
            let x = xd.x[di][k];
            let val = |j: usize| if sign > 0 { mv[j].up } else { mv[j].dn };
            let start = |j: usize| if sign > 0 { mv[j].iu } else { mv[j].id };
            let cands: Vec<(usize, usize, f64)> = (0..n)
                .filter(|&j| val(j) >= x)
                .map(|j| (j, start(j), val(j)))
                .collect();
            let mut order: Vec<usize> = (0..cands.len()).collect();
            order.sort_by(|&a, &b| {
                cands[b]
                    .2
                    .partial_cmp(&cands[a].2)
                    .expect("ход без NaN")
                    .then(cands[a].0.cmp(&cands[b].0))
            });
            let mut taken: Vec<(usize, usize)> = Vec::new();
            for &pos in &order {
                let (j, i, v) = cands[pos];
                if taken.iter().any(|&(s, t)| s <= j && t >= i) {
                    continue;
                }
                taken.push((i, j));
                let ret = ff[j] - (ff[j] - ff[i]) * e.e4_wick.return_share;
                let last = (j + 1 + e.e4_wick.within_min as usize).min(n);
                let wick =
                    (j + 1..last).any(|m| if sign > 0 { ff[m] <= ret } else { ff[m] >= ret });
                let (mut a, mut b) = (j, j);
                while a > 0 && val(a - 1) >= x {
                    a -= 1;
                }
                while b + 1 < n && val(b + 1) >= x {
                    b += 1;
                }
                let mut flags = 0;
                if wick {
                    flags |= FLAG_WICK;
                }
                if ((b - a + 1) as u32) < e.outlier_tail_candles_lt {
                    flags |= FLAG_OUTLIER;
                }
                out.push(Episode {
                    di: di as u8,
                    sign,
                    t0: c.first_min + i as i64,
                    t_end: c.first_min + j as i64,
                    mv: v,
                    flags,
                });
            }
        }
    }
    out.sort_by_key(|x| (x.t0, x.di, x.sign));
    out
}

/// Квантиль отсортированного ряда по `numpy.quantile(method="linear")`: `h = (n − 1)·q`, `_lerp`.
pub fn quantile_linear(sorted: &[f64], q: f64) -> f64 {
    let n = sorted.len();
    if n == 0 {
        return f64::NAN;
    }
    let h = (n - 1) as f64 * q;
    if h >= (n - 1) as f64 {
        return sorted[n - 1];
    }
    let lo = h.floor() as usize;
    let t = h - lo as f64;
    let (a, b) = (sorted[lo], sorted[lo + 1]);
    let diff = b - a;
    if t >= 0.5 {
        b - diff * (1.0 - t)
    } else {
        a + diff * t
    }
}

/// Все ходы концов `j` с минутой в `[fit_min[0], fit_min[1])` по всем контрактам, по `[di][вверх|вниз]`, по возрастанию.
/// Контракты с плечевыми именами вызывающий не передаёт.
pub fn xd_values_naive(
    contracts: &[&Contract],
    p: &Params,
    fit_min: [i64; 2],
) -> Vec<[Vec<f64>; 2]> {
    let nd = p.episodes.d_min.len();
    let mut vals: Vec<[Vec<f64>; 2]> = vec![Default::default(); nd];
    for c in contracts {
        let (_, lc) = ff_ln(c);
        for (di, &d) in p.episodes.d_min.iter().enumerate() {
            for (j, m) in moves_naive(&lc, d as usize).into_iter().enumerate() {
                let minute = c.first_min + j as i64;
                if (fit_min[0]..fit_min[1]).contains(&minute) && !m.up.is_nan() {
                    vals[di][0].push(m.up);
                    vals[di][1].push(m.dn);
                }
            }
        }
    }
    for v in vals.iter_mut() {
        for s in v.iter_mut() {
            s.sort_by(f64::total_cmp);
        }
    }
    vals
}

/// Таблица `X_D` из отсортированных значений: квантиль `q` по `[di][знак]`, `NaN` для пустых.
pub fn xd_from_values(vals: &[[Vec<f64>; 2]], q: f64) -> XdTable {
    XdTable {
        x: vals
            .iter()
            .map(|v| [quantile_linear(&v[0], q), quantile_linear(&v[1], q)])
            .collect(),
    }
}

/// `X_D` в лоб: все ходы за фит, сортировка, квантиль `episodes.quantile_X_D`.
pub fn xd_naive(contracts: &[&Contract], p: &Params, fit_min: [i64; 2]) -> XdTable {
    xd_from_values(
        &xd_values_naive(contracts, p, fit_min),
        p.episodes.quantile_x_d,
    )
}

/// `mark_market` перебором всех пар.
pub fn mark_market_naive(eps: &mut [Episode], btc: &[Episode], min_overlap_share: f64) {
    for e in eps.iter_mut() {
        let len = (e.t_end - e.t0) as f64;
        let hit = btc.iter().any(|b| {
            let ov = e.t_end.min(b.t_end) - e.t0.max(b.t0);
            b.sign == e.sign && b.di == e.di && ov > 0 && ov as f64 / len >= min_overlap_share
        });
        if hit {
            e.flags |= FLAG_MARKET;
        }
    }
}
