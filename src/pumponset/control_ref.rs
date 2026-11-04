//! Наивные эталоны контролей и лагов (TK-492): определения П-34 §4–§5 «в лоб» — метка вперёд перебором
//! `s` по окну каждого `D` (`O(n·D)`), лаги перебором минут, кандидаты C2a/C2b — перебором всех слотов куба
//! с проверкой условий по определению. Служат только сверке `control.rs` (`control_tests.rs`); в прогон не входят.
//! Общие с боевым кодом — генератор, выбор без повторов и ключи потоков (иначе сверять нечего: сверяются
//! построение множеств кандидатов, дециль/месяц, метки и лаги).

#![allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::cache::SLOT_MIN;
use super::control::{
    ep_key, pick, rule_features, rule_tag, seed_for, sign_of, slot_t, stream, yyyymm, C2bRow, Input,
    Label, TriggerRow, DECILES, TAG_C2A, TAG_C2B,
};
use super::params::Params;
use super::types::{Cube, Episode, LagRow, Rule, XdTable};
use anyhow::Result;

/// Метка вперёд по определению: для каждого `D ≤ h` — максимум знаковой `ln(c[s]/c[t_f+1])` по `s` из
/// `[t_f+1, t_f+D]`, сравнение с `X_D`. `NoData` — как у `control::label` (по индексам, допущение 5).
pub fn label_ref(
    cf: &[f64],
    first_min: i64,
    t_f: i64,
    sign: i8,
    xd: &XdTable,
    p: &Params,
    h: u32,
) -> Label {
    if sign == 0 {
        return Label::NoData;
    }
    let sg = if sign > 0 { 1.0 } else { -1.0 };
    let ds: Vec<u32> = p
        .label_forward
        .d_min
        .iter()
        .copied()
        .filter(|&d| d <= h)
        .collect();
    let Some(&dmax) = ds.iter().max() else {
        return Label::NoData;
    };
    let b = t_f + 1 - first_min;
    let last_min = cf.len() as i64 - 1;
    if b < 0 || b + i64::from(dmax) - 1 > last_min || cf[b as usize].is_nan() {
        return Label::NoData;
    }
    let b = b as usize;
    for &d in &ds {
        let Some(di) = p.episodes.d_min.iter().position(|&x| x == d) else {
            continue;
        };
        let x = xd.x[di][usize::from(sign < 0)];
        let best = cf[b..b + d as usize]
            .iter()
            .map(|&c| sg * (c / cf[b]).ln())
            .fold(f64::NEG_INFINITY, f64::max);
        if best >= x {
            return Label::Hit;
        }
    }
    Label::Miss
}

/// Первое срабатывание перебором минут `[t0 − l_pre, t0 + l_post]` (слот — минуты, кратные 5).
fn scan_minutes(
    cube: &Cube,
    t0: i64,
    l_pre: i64,
    l_post: i64,
    mut fires: impl FnMut(usize) -> bool,
) -> Option<i64> {
    for m in (t0 - l_pre)..=(t0 + l_post) {
        if m.rem_euclid(SLOT_MIN) != 0 {
            continue;
        }
        let i = m / SLOT_MIN - cube.slot0;
        if !(0..cube.n as i64).contains(&i) {
            continue;
        }
        if fires(i as usize) {
            return Some(m - t0);
        }
    }
    None
}

/// Лаги по определению (порядок строк как у `control::lags_with`).
pub fn lags_ref(
    cube: &Cube,
    eps: &[Episode],
    rules: &[Rule],
    p: &Params,
    l_post_override: Option<i64>,
    mut fires: impl FnMut(Rule, usize) -> bool,
) -> Vec<LagRow> {
    let l_pre = i64::from(p.onset.l_pre_min);
    let mut out = Vec::new();
    for (ei, e) in eps.iter().enumerate() {
        let l_post = l_post_override.unwrap_or(e.t_end - e.t0);
        for &rule in rules {
            out.push(LagRow {
                rule,
                episode: ei as u32,
                lag: scan_minutes(cube, e.t0, l_pre, l_post, |i| fires(rule, i)),
            });
        }
    }
    out
}

/// Дециль σ каждого слота по определению (порезы `sorted[j·m/10]`, дециль = число порезов ≤ σ).
fn decile_ref(sigma: &[f32]) -> Vec<Option<usize>> {
    let mut v: Vec<f32> = sigma.iter().copied().filter(|x| x.is_finite()).collect();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let m = v.len();
    sigma
        .iter()
        .map(|&x| {
            x.is_finite()
                .then(|| (1..DECILES).filter(|&j| v[j * m / DECILES] <= x).count())
        })
        .collect()
}

/// C2a по определению: кандидаты — все слоты куба, подходящие по месяцу, децилю σ, знаку и признакам правила.
pub fn c2a_ref(
    inp: &Input,
    rules: &[Rule],
    xd: &XdTable,
    p: &Params,
    seed: u64,
    mut first_triggers: impl FnMut(Rule) -> Vec<usize>,
) -> Result<Vec<TriggerRow>> {
    let cube = inp.cube;
    let h_min = &p.label_forward.h_min;
    let nh = h_min.len();
    let k_want = p.controls.c2a.moments_per_first_trigger as usize;
    let dec = decile_ref(&cube.sigma);
    let mut out = Vec::new();
    for &rule in rules {
        let wi = rule.wi as usize;
        let feats = rule_features(rule.fam);
        let rseed = seed_for(seed, inp.sym, rule_tag(TAG_C2A, rule));
        for i in first_triggers(rule) {
            let t_f = slot_t(cube, i);
            let sign = sign_of(cube.rat(wi, i));
            let label_at = |t: i64, s: i8, h: u32| label_ref(inp.cf, inp.first_min, t, s, xd, p, h);
            let (mut hit, mut cnt, mut drawn) = (vec![0u16; nh], vec![0u16; nh], 0u16);
            if sign != 0 {
                if let Some(d) = dec[i] {
                    let cand: Vec<usize> = (0..cube.n)
                        .filter(|&j| {
                            yyyymm(slot_t(cube, j)) == yyyymm(t_f)
                                && dec[j] == Some(d)
                                && sign_of(cube.rat(wi, j)) != 0
                                && feats.iter().all(|&f| !cube.zat(f, wi, j).is_nan())
                        })
                        .collect();
                    let k = cand.len().min(k_want);
                    let mut idx = Vec::new();
                    pick(&mut stream(rseed, t_f as u64), cand.len(), k, &mut idx);
                    drawn = k as u16;
                    for &q in &idx {
                        let j = cand[q as usize];
                        let (tj, sj) = (slot_t(cube, j), sign_of(cube.rat(wi, j)));
                        for (hi, &h) in h_min.iter().enumerate() {
                            match label_at(tj, sj, h) {
                                Label::Hit => {
                                    hit[hi] += 1;
                                    cnt[hi] += 1;
                                }
                                Label::Miss => cnt[hi] += 1,
                                Label::NoData => {}
                            }
                        }
                    }
                }
            }
            out.push(TriggerRow {
                rule,
                t_f,
                month: yyyymm(t_f),
                sign,
                label: h_min.iter().map(|&h| label_at(t_f, sign, h)).collect(),
                c2a_hit: hit,
                c2a_n: cnt,
                c2a_drawn: drawn,
                in_episode_running: sign != 0
                    && inp
                        .eps
                        .iter()
                        .any(|e| e.sign == sign && e.t0 < t_f && t_f <= e.t_end),
            });
        }
    }
    Ok(out)
}

/// C2b по определению: кандидаты — все слоты куба того же месяца, с тем же децилем σ «до» (`t − L_pre`), окном
/// лага внутри куба и без эпизода того же знака в `[t + a, t + b]`.
pub fn c2b_ref(
    inp: &Input,
    rules: &[Rule],
    p: &Params,
    l_post: i64,
    seed: u64,
    mut fires: impl FnMut(Rule, usize) -> bool,
) -> Vec<C2bRow> {
    let cube = inp.cube;
    let l_pre = i64::from(p.onset.l_pre_min);
    let [wa, wb] = p.controls.c2b.exclude_same_sign_episode_window_min;
    let k_want = p.controls.c2b.moments_per_episode as usize;
    let dec = decile_ref(&cube.sigma);
    let cseed = seed_for(seed, inp.sym, TAG_C2B);
    let n = cube.n as i64;
    let dec_before = |t: i64| -> Option<usize> {
        let r = (t - l_pre).div_euclid(SLOT_MIN) - cube.slot0;
        if (0..n).contains(&r) {
            dec[r as usize]
        } else {
            None
        }
    };
    let mut out = Vec::new();
    for (ei, e) in inp.eps.iter().enumerate() {
        if e.sign == 0 || cube.n == 0 {
            continue;
        }
        let Some(de) = dec_before(e.t0) else { continue };
        let t_last = slot_t(cube, cube.n - 1);
        let ym = yyyymm(e.t0);
        let cand: Vec<usize> = (0..cube.n)
            .filter(|&j| {
                let t = slot_t(cube, j);
                yyyymm(t) == ym
                    && dec_before(t) == Some(de)
                    && t + l_post <= t_last
                    && !inp
                        .eps
                        .iter()
                        .any(|x| x.sign == e.sign && (t + wa..=t + wb).contains(&x.t0))
            })
            .collect();
        let k = cand.len().min(k_want);
        let mut idx = Vec::new();
        pick(&mut stream(cseed, ep_key(e)), cand.len(), k, &mut idx);
        for &q in &idx {
            let tp = slot_t(cube, cand[q as usize]);
            for &rule in rules {
                out.push(C2bRow {
                    rule,
                    episode_idx: ei as u32,
                    t_pseudo: tp,
                    lag: scan_minutes(cube, tp, l_pre, l_post, |i| fires(rule, i)),
                });
            }
        }
    }
    out
}
