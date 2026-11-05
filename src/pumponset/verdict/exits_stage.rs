//! Стадии `exits-fit` / `exits-test` команды `verdict` (П-35, `docs/research/P-35-exits.md`): выбор выхода на фите и
//! проверка замороженной пары (правило, сторона → конфигурация) на тесте. Читает `exits-<stage>.csv` и
//! `exits-grid.csv` (их пишет `run --exits`), файл параметров и файл предыдущей стадии; издержки — `crate::lob::costs`.
//!
//! # Допущения (то, чего протокол не называет)
//! 1. **Файлы `--k`.** `exits-fit`: `k.json` стадии `fit` П-34 (правила K — по ним строки `exits-fit.csv` отбираются,
//!    лишние отбрасываются и считаются). `exits-test`: `exits-k.json`, который записал `exits-fit` (замороженные пары).
//! 2. **Выбор.** Для каждой пары (правило ∈ K, сторона) — конфигурация с наибольшей нижней границей 95 % среднего
//!    net на сделку (основной вариант издержек, `2·leg_fee_bps(true)`), бутстрэп по суткам как в `verdict.rs`; ничья
//!    (граница равна точно) — меньший `cap`, затем конфигурация в bps раньше конфигурации в единицах ATR-прокси
//!    (стопы `S` в разных единицах несравнимы; это соглашение о порядке, а не утверждение), затем меньший `S` (стоп
//!    «нет» — самый большой), затем меньший номер. Выбор идёт по всем 140 конфигурациям сетки (84 в bps и 56 в единицах
//!    A; у последних `n` меньше — сделки без определённой A в них не входят). Пара без сделок ни в одной конфигурации
//!    в выбор не идёт (`no_data`) и в тесте считается непрошедшей.
//! 3. **net** на сделку = `(Σ gross − Σ фандинг)/n − 2·leg_fee_bps(true)`; второй вариант — `ROUNDTRIP_FEES_BPS`.
//!    Односторонний `p` (net > 0) и интервал — как у net в `verdict.rs`; Холм по `trials.test_net_m` на замороженных парах.
//! 4. **Месяцы теста.** Знаменатель доли месяцев с net > 0 — все месяцы `split.test`, а не присутствующие в файле;
//!    месяц без сделок пары — не «net > 0» (как у net в `verdict.rs`, допущение 4).
//! 5. **Итог «выход в прибыль найден»** (П-35 «Отбор и проверка», правило §7.6 П-34 без условия по lift): нижняя
//!    граница 95 % net > 0, p-Холм < `holm_alpha`, доля месяцев net > 0 не меньше `min_months_share`. Пампы и дампы —
//!    отдельные итоги. Подбор из 140 конфигураций в m теста не входит (подбор только на фите).
//! 6. **Доля mfe, которую забрали** = `Σ gross / Σ mfe` ячейки; в таблицах две: по потолку окна 1440 мин (литерально
//!    П-35) и по окну собственного `cap`. Описательно, в правило не входит.
//! 7. **Месяцы вне периода.** Фит: любой месяц вне `split.fit` — отказ (тест не должен попасть в выбор). Тест: месяц
//!    раньше начала — отказ, позже конца — строки отбрасываются (число — в отчёт).
//! 8. **Лучшая в семействе.** Кроме замороженной пары `exits-fit` для каждой пары (правило, сторона) выбирает по тому же
//!    порядку предпочтения лучшую конфигурацию в каждой группе — `fixed` (tp + trail), `atr` (tpa + tra), tp, trail,
//!    tpa, tra (базовая линия в группы не входит) — и пишет их в `exits-k.json` (`best_by_group`) и
//!    `exits-verdict-fit-groups.csv`; `exits-test` только оценивает эти конфигурации на тесте
//!    (`exits-verdict-test-groups.csv`, раздел 3 отчёта) и показывает `atr − fixed`. Описательно, без Холма и вне m
//!    теста: подбор лишь на фите, на тесте ничего не выбирается. Без `best_by_group` в `exits-k.json` раздел не строится.
//! 9. **Формат сетки.** `exits-grid.csv` содержит колонку `unit` (`bps` | `atr`); файл прежней версии (84 конфигурации,
//!    без `unit`) не совпадёт с сеткой кода и будет отвергнут — таблицы надо пересчитать (`run --exits`).

use super::{
    f6, fam_name, field, md_row, md_sep, month_of, period_months, pos, r3, ratio, read_csv, read_k,
    rule_label, share_ok, sign_of, write_lines, Doc, VParams, VerdictArgs,
};
use crate::lob::costs::{leg_fee_bps, ROUNDTRIP_FEES_BPS};
use crate::pumponset::exits as xg;
use crate::pumponset::stats::{bootstrap, holm, interval, Sparse};
use crate::pumponset::types::Rule;
use anyhow::{anyhow, bail, Context, Result};
use std::cmp::Ordering;
use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

// ---------- ключи, строки, чтение ----------

/// Ячейка: правило, сторона, конфигурация. Порядок — как в csv (`fam,wi,ti,sign,cfg`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct XKey {
    fam: u8,
    wi: u8,
    ti: u8,
    sign: i8,
    cfg: u8,
}

impl XKey {
    fn rule(self) -> Rule {
        Rule {
            fam: self.fam,
            wi: self.wi,
            ti: self.ti,
        }
    }
}

/// Число суммируемых колонок строки: n, gross, fund, hold, tp, sl, trail, cap, mfe, mfe_min, mfe_cap, mfe_cap_min.
const NV: usize = 12;
const V_N: usize = 0;
const V_GROSS: usize = 1;
const V_FUND: usize = 2;
const V_HOLD: usize = 3;
const V_TP: usize = 4;
const V_SL: usize = 5;
const V_TR: usize = 6;
const V_CAP: usize = 7;
const V_MFE: usize = 8;
const V_MFE_MIN: usize = 9;
const V_MFEC: usize = 10;

struct XRow {
    key: XKey,
    day: i64,
    month: i32,
    v: [f64; NV],
}

fn parse_row(rec: &csv::StringRecord) -> Result<XRow> {
    let cfg: u8 = field(rec, 4, "cfg")?;
    if usize::from(cfg) >= xg::NCFG {
        bail!("cfg = {cfg} вне сетки (0..{})", xg::NCFG);
    }
    let key = XKey {
        fam: field(rec, 0, "fam")?,
        wi: field(rec, 1, "wi")?,
        ti: field(rec, 2, "ti")?,
        sign: sign_of(rec, 3)?,
        cfg,
    };
    let (n, hold, tp, sl, tr, cap): (u64, u64, u64, u64, u64, u64) = (
        field(rec, 7, "n")?,
        field(rec, 10, "hold_min_sum")?,
        field(rec, 11, "n_tp")?,
        field(rec, 12, "n_sl")?,
        field(rec, 13, "n_trail")?,
        field(rec, 14, "n_cap")?,
    );
    if n == 0 || tp + sl + tr + cap != n {
        bail!("n = {n}, а причины выхода дают {}", tp + sl + tr + cap);
    }
    let (gross, fund, mfe, mfec): (f64, f64, f64, f64) = (
        field(rec, 8, "gross_bps_sum")?,
        field(rec, 9, "fund_bps_sum")?,
        field(rec, 15, "mfe_bps_sum")?,
        field(rec, 17, "mfe_cap_bps_sum")?,
    );
    if ![gross, fund, mfe, mfec].iter().all(|x| x.is_finite()) {
        bail!("суммы bps не конечны");
    }
    let (mfe_min, mfec_min): (u64, u64) = (
        field(rec, 16, "mfe_min_sum")?,
        field(rec, 18, "mfe_cap_min_sum")?,
    );
    Ok(XRow {
        key,
        day: field(rec, 5, "day")?,
        month: field(rec, 6, "month")?,
        v: [
            n as f64,
            gross,
            fund,
            hold as f64,
            tp as f64,
            sl as f64,
            tr as f64,
            cap as f64,
            mfe,
            mfe_min as f64,
            mfec,
            mfec_min as f64,
        ],
    })
}

fn read_exits(path: &Path) -> Result<Vec<XRow>> {
    let head: Vec<&str> = xg::exits_head().split(',').collect();
    read_csv(path, &head)?
        .iter()
        .enumerate()
        .map(|(i, rec)| {
            parse_row(rec).with_context(|| format!("{} строка {}", path.display(), i + 2))
        })
        .collect()
}

/// `exits-grid.csv` рядом с таблицами должен совпасть с сеткой кода (нумерация конфигураций стабильна).
fn check_grid(dir: &Path) -> Result<()> {
    let p = dir.join("exits-grid.csv");
    let text = fs::read_to_string(&p).with_context(|| p.display().to_string())?;
    if text.replace("\r\n", "\n") != xg::grid_csv() {
        bail!(
            "{}: сетка файла не совпадает с сеткой кода (exits.rs) — таблицы посчитаны другой версией",
            p.display()
        );
    }
    Ok(())
}

/// Допущение 7. Возвращает строки периода и число отброшенных (только тест, позже конца).
fn guard_rows(rows: Vec<XRow>, test: bool, vp: &VParams) -> Result<(Vec<XRow>, usize)> {
    let r = if test { &vp.split.test } else { &vp.split.fit };
    let (lo, hi) = (month_of(&r[0])?, month_of(&r[1])?);
    let name = if test { "test" } else { "fit" };
    let mut kept = Vec::with_capacity(rows.len());
    let mut dropped = 0usize;
    for row in rows {
        if row.month < lo || (row.month > hi && !test) {
            bail!(
                "месяц {} вне split.{name} [{lo}…{hi}]: стадия читает только свой период",
                row.month
            );
        }
        if row.month > hi {
            dropped += 1;
        } else {
            kept.push(row);
        }
    }
    Ok((kept, dropped))
}

// ---------- таблица ----------

struct XTable {
    keys: Vec<XKey>,
    /// Суток-кластеров: объединение суток по ВСЕМ строкам файла (до отбора ячеек).
    days: usize,
    /// `[n, gross, fund]` по суткам для бутстрэпа.
    net: Sparse<3>,
    /// Суммы всех колонок ячейки.
    tot: Vec<[f64; NV]>,
    /// `[ячейка][месяц файла]` → суммы всех колонок.
    monthly: Vec<Vec<[f64; NV]>>,
}

/// `want` — ячейки, которые надо вернуть (даже пустыми).
fn build_xtable(rows: &[XRow], want: &[XKey]) -> Result<XTable> {
    let mut days: Vec<i64> = rows.iter().map(|r| r.day).collect();
    days.sort_unstable();
    days.dedup();
    let mut months: Vec<i32> = rows.iter().map(|r| r.month).collect();
    months.sort_unstable();
    months.dedup();
    let mut keys = want.to_vec();
    keys.sort_unstable();
    keys.dedup();
    let mut sel: Vec<&XRow> = rows
        .iter()
        .filter(|r| keys.binary_search(&r.key).is_ok())
        .collect();
    sel.sort_by(|a, b| a.key.cmp(&b.key).then(a.day.cmp(&b.day)));
    for w in sel.windows(2) {
        if w[0].key == w[1].key && w[0].day == w[1].day {
            bail!(
                "повтор строки: fam={} wi={} ti={} sign={} cfg={} day={}",
                w[0].key.fam,
                w[0].key.wi,
                w[0].key.ti,
                w[0].key.sign,
                w[0].key.cfg,
                w[0].day
            );
        }
    }
    let mut net = Sparse::<3>::new();
    let mut tot = Vec::with_capacity(keys.len());
    let mut monthly = Vec::with_capacity(keys.len());
    let mut i = 0usize;
    for key in &keys {
        let mut mm = vec![[0.0f64; NV]; months.len()];
        let mut tt = [0.0f64; NV];
        while i < sel.len() && sel[i].key == *key {
            let r = sel[i];
            let di = pos(&days, &r.day, "сутки")?;
            net.push(di as u32, [r.v[V_N], r.v[V_GROSS], r.v[V_FUND]]);
            let m = &mut mm[pos(&months, &r.month, "месяц")?];
            for ((a, t), b) in m.iter_mut().zip(tt.iter_mut()).zip(r.v) {
                *a += b;
                *t += b;
            }
            i += 1;
        }
        net.end_cell();
        tot.push(tt);
        monthly.push(mm);
    }
    Ok(XTable {
        keys,
        days: days.len(),
        net,
        tot,
        monthly,
    })
}

// ---------- net ----------

#[derive(Debug, Clone, Copy)]
struct XNet {
    n: f64,
    gross: f64,
    fund: f64,
    hold: f64,
    main: f64,
    lo: f64,
    hi: f64,
    p: f64,
    second: f64,
    second_lo: f64,
    second_hi: f64,
    valid: usize,
}

fn x_results(t: &XTable, vp: &VParams, threads: usize) -> Result<Vec<XNet>> {
    let v = &vp.verdict;
    let (fee_main, fee_second) = (2.0 * leg_fee_bps(true), ROUNDTRIP_FEES_BPS);
    let reps = bootstrap::<3, 2, _>(&t.net, t.days, v.bootstrap_b, v.seed, threads, |s, out| {
        if s[0] > 0.0 {
            let m = (s[1] - s[2]) / s[0];
            out[0] = m - fee_main;
            out[1] = m - fee_second;
        }
    })?;
    Ok((0..t.keys.len())
        .map(|c| {
            let a = &t.tot[c];
            let n = a[V_N];
            let (gm, fm) = (ratio(a[V_GROSS], n), ratio(a[V_FUND], n));
            let l = interval(reps.get(c, 0), v.ci_level, 0.0);
            let l2 = interval(reps.get(c, 1), v.ci_level, 0.0);
            XNet {
                n,
                gross: gm,
                fund: fm,
                hold: ratio(a[V_HOLD], n),
                main: gm - fm - fee_main,
                lo: l.lo,
                hi: l.hi,
                p: l.p,
                second: gm - fm - fee_second,
                second_lo: l2.lo,
                second_hi: l2.hi,
                valid: l.valid,
            }
        })
        .collect())
}

/// Месяцы ячейки с `n > 0` и с net > 0 (основной вариант издержек).
fn month_pos(t: &XTable, c: usize) -> (usize, usize) {
    let fee = 2.0 * leg_fee_bps(true);
    let (mut def, mut up) = (0, 0);
    for m in &t.monthly[c] {
        if m[V_N] > 0.0 {
            def += 1;
            if (m[V_GROSS] - m[V_FUND]) / m[V_N] - fee > 0.0 {
                up += 1;
            }
        }
    }
    (up, def)
}

// ---------- описание конфигурации ----------

fn cfg_label(grid: &[xg::Cfg], k: u8) -> String {
    let c = &grid[usize::from(k)];
    let (g, s, r) = c.gsr_cols();
    let s = if s.is_empty() { "none".to_string() } else { s };
    match c.family {
        xg::Family::Base => format!("base cap{}", c.cap()),
        xg::Family::Tp => format!("tp G{g} S{s} cap{}", c.cap()),
        xg::Family::Trail => format!("trail R{r} S{s} cap{}", c.cap()),
        // множители при A: «G2.0A» = 2,0·A
        xg::Family::TpA => format!("tpa G{g}A S{}cap{}", atr_stop(&s), c.cap()),
        xg::Family::TrA => format!("tra R{r}A S{}cap{}", atr_stop(&s), c.cap()),
    }
}

/// Стоп семейств C и D для подписи: `2.0A ` или `none `.
fn atr_stop(s: &str) -> String {
    if s == "none" {
        "none ".to_string()
    } else {
        format!("{s}A ")
    }
}

/// Колонки параметров конфигурации в csv: `cfg,family,G,S,R,cap,unit` (как `exits-grid.csv`).
fn cfg_cols(grid: &[xg::Cfg], k: u8) -> String {
    let c = &grid[usize::from(k)];
    let (g, s, r) = c.gsr_cols();
    format!(
        "{},{},{},{},{},{},{}",
        k,
        c.family.name(),
        g,
        s,
        r,
        c.cap(),
        c.family.unit()
    )
}

const CFG_COLS: &str = "cfg,family,G,S,R,cap,unit";

/// Группы «лучшая в семействе» (допущение 8): fixed (tp, trail) против atr (tpa, tra) и каждое из четырёх семейств.
/// Базовая линия (только cap) в группы не входит — она есть в полной таблице.
const GROUPS: [(&str, &[xg::Family]); 6] = [
    ("fixed", &[xg::Family::Tp, xg::Family::Trail]),
    ("atr", &[xg::Family::TpA, xg::Family::TrA]),
    ("tp", &[xg::Family::Tp]),
    ("trail", &[xg::Family::Trail]),
    ("tpa", &[xg::Family::TpA]),
    ("tra", &[xg::Family::TrA]),
];
const NGROUP: usize = GROUPS.len();

/// Параметр конфигурации в `exits-k.json`: bps — целое, множитель A — число с точкой, нет — `null`.
fn gsr_json(bps: Option<u32>, mult: Option<f64>) -> serde_json::Value {
    match (bps, mult) {
        (Some(v), _) => serde_json::Value::from(v),
        (None, Some(m)) => serde_json::Value::from(m),
        (None, None) => serde_json::Value::Null,
    }
}

// ---------- стадия exits-fit ----------

/// Порядок предпочтения (допущение 2): больше нижняя граница, затем меньший `cap`, затем конфигурация в bps раньше
/// конфигурации в единицах A (стопы в разных единицах несравнимы — соглашение, не утверждение), затем меньший `S`
/// (внутри одной единицы; «нет» — наибольший), затем меньший номер.
fn pref(grid: &[xg::Cfg], a: (XKey, &XNet), b: (XKey, &XNet)) -> Ordering {
    let (ca, cb) = (grid[usize::from(a.0.cfg)], grid[usize::from(b.0.cfg)]);
    let s = |c: &xg::Cfg| {
        c.s_bps()
            .map(f64::from)
            .or(c.s_mult())
            .unwrap_or(f64::INFINITY)
    };
    b.1.lo
        .total_cmp(&a.1.lo)
        .then(ca.cap().cmp(&cb.cap()))
        .then(ca.family.is_atr().cmp(&cb.family.is_atr()))
        .then(s(&ca).total_cmp(&s(&cb)))
        .then(a.0.cfg.cmp(&b.0.cfg))
}

/// Лучшая по `pref` ячейка пары (правило, сторона) среди конфигураций группы `gi` с определённой нижней границей.
fn best_in_group(
    t: &XTable,
    res: &[XNet],
    grid: &[xg::Cfg],
    rule: Rule,
    sign: i8,
    gi: usize,
) -> Option<usize> {
    let fams = GROUPS[gi].1;
    t.keys
        .iter()
        .enumerate()
        .filter(|(c, k)| {
            k.rule() == rule
                && k.sign == sign
                && res[*c].lo.is_finite()
                && fams.contains(&grid[usize::from(k.cfg)].family)
        })
        .min_by(|x, y| pref(grid, (*x.1, &res[x.0]), (*y.1, &res[y.0])))
        .map(|(c, _)| c)
}

pub(super) fn fit_stage(a: &VerdictArgs, vp: &VParams, sha: &str) -> Result<()> {
    let kpath =
        a.k.as_ref()
            .context("--k (k.json фита П-34) обязателен для стадии exits-fit")?;
    let krules = read_k(kpath, vp)?;
    check_grid(&a.input)?;
    let (rows, _) = guard_rows(read_exits(&a.input.join("exits-fit.csv"))?, false, vp)?;
    let not_k = rows
        .iter()
        .filter(|r| !krules.contains(&r.key.rule()))
        .count();
    let grid = xg::grid();
    let mut want: Vec<XKey> = Vec::new();
    for r in &krules {
        for sign in [-1i8, 1] {
            for cfg in 0..xg::NCFG as u8 {
                want.push(XKey {
                    fam: r.fam,
                    wi: r.wi,
                    ti: r.ti,
                    sign,
                    cfg,
                });
            }
        }
    }
    let t = build_xtable(&rows, &want)?;
    let res = x_results(&t, vp, a.threads)?;

    // выбор: по паре (правило, сторона) — лучшая конфигурация из всех 140; отдельно — лучшая в каждой группе
    // семейств (допущение 8), тоже по фиту: на тесте они оцениваются как замороженные
    let mut chosen: Vec<(Rule, i8, usize)> = Vec::new();
    let mut groups: Vec<[Option<usize>; NGROUP]> = Vec::new();
    let mut no_data = 0usize;
    for r in &krules {
        for sign in [1i8, -1] {
            let best = t
                .keys
                .iter()
                .enumerate()
                .filter(|(c, k)| k.rule() == *r && k.sign == sign && res[*c].lo.is_finite())
                .min_by(|x, y| pref(&grid, (*x.1, &res[x.0]), (*y.1, &res[y.0])));
            match best {
                Some((c, _)) => {
                    chosen.push((*r, sign, c));
                    groups.push(std::array::from_fn(|gi| {
                        best_in_group(&t, &res, &grid, *r, sign, gi)
                    }));
                }
                None => no_data += 1,
            }
        }
    }

    let mut vl = vec![format!(
        "fam,wi,ti,theta,w_min,sign,{CFG_COLS},n,gross_mean,fund_mean,hold_mean,net_main,net_lo,net_hi,b_valid,chosen"
    )];
    for (c, k) in t.keys.iter().enumerate() {
        let r = &res[c];
        vl.push(
            [
                k.fam.to_string(),
                k.wi.to_string(),
                k.ti.to_string(),
                vp.theta(k.ti).to_string(),
                vp.window(k.wi).to_string(),
                k.sign.to_string(),
                cfg_cols(&grid, k.cfg),
                r.n.to_string(),
                f6(r.gross),
                f6(r.fund),
                f6(r.hold),
                f6(r.main),
                f6(r.lo),
                f6(r.hi),
                r.valid.to_string(),
                u8::from(chosen.iter().any(|x| x.2 == c)).to_string(),
            ]
            .join(","),
        );
    }
    write_lines(&a.out.join("exits-verdict-fit.csv"), &vl)?;
    write_groups_fit(a, vp, &grid, &t, &res, &chosen, &groups)?;

    let kj: Vec<serde_json::Value> = chosen
        .iter()
        .zip(&groups)
        .enumerate()
        .map(|(i, ((rule, sign, c), gr))| {
            let (k, r) = (t.keys[*c], &res[*c]);
            let g = &grid[usize::from(k.cfg)];
            // лучшая в группе: номер конфигурации или null
            let best: serde_json::Map<String, serde_json::Value> = GROUPS
                .iter()
                .zip(gr)
                .map(|((name, _), cell)| {
                    (
                        (*name).to_string(),
                        cell.map_or(serde_json::Value::Null, |c| {
                            serde_json::Value::from(t.keys[c].cfg)
                        }),
                    )
                })
                .collect();
            serde_json::json!({
                "pos": i + 1,
                "fam": rule.fam,
                "wi": rule.wi,
                "ti": rule.ti,
                "family": fam_name(rule.fam),
                "theta": vp.theta(rule.ti),
                "w_min": vp.window(rule.wi),
                "sign": sign,
                "cfg": k.cfg,
                "exit_family": g.family.name(),
                "unit": g.family.unit(),
                "G": gsr_json(g.g_bps(), g.g_mult()),
                "S": gsr_json(g.s_bps(), g.s_mult()),
                "R": gsr_json(g.r_bps(), g.r_mult()),
                "cap": g.cap(),
                "n": r.n,
                "net": r.main,
                "net_lo": r.lo,
                "net_hi": r.hi,
                "best_by_group": best,
            })
        })
        .collect();
    let mut kt = serde_json::to_string_pretty(&serde_json::Value::Array(kj))?;
    kt.push('\n');
    fs::write(a.out.join("exits-k.json"), kt)?;

    let list: Vec<String> = chosen
        .iter()
        .map(|(rule, sign, c)| {
            format!(
                "{} {:+} → cfg {} ({}) net_lo={:.3}",
                rule_label(vp, *rule),
                sign,
                t.keys[*c].cfg,
                cfg_label(&grid, t.keys[*c].cfg),
                res[*c].lo
            )
        })
        .collect();
    let line = format!(
        "П-35 · фит {}…{} · замороженные пары (правило, сторона → выход) до открытия теста (params sha256 {}): {}. \
         Выбор = наибольшая нижняя граница {:.0} % среднего net на сделку (издержки 2·leg_fee_bps(true), реальный фандинг); ничья — меньший cap, \
         затем конфигурация в bps раньше конфигурации в единицах ATR-прокси, затем меньший S; бутстрэп по суткам B={}, seed={}. \
         Пар (правило×сторона) {}, без данных {}; ячеек (пара × {} конфигураций) {}; \
         строк вне K отброшено {}. Подбор только на фите, в m теста не входит. Лучшая в семействе (fixed против atr) на фите — \
         exits-verdict-fit-groups.csv. Файлы: exits-k.json, exits-verdict-fit.csv.",
        vp.split.fit[0],
        vp.split.fit[1],
        &sha[..sha.len().min(12)],
        list.join("; "),
        vp.verdict.ci_level * 100.0,
        vp.verdict.bootstrap_b,
        vp.verdict.seed,
        krules.len() * 2,
        no_data,
        xg::NCFG,
        t.keys.len(),
        not_k
    );
    fs::write(a.out.join("exits-journal-line.txt"), format!("{line}\n"))?;
    println!("{line}");
    Ok(())
}

/// `exits-verdict-fit-groups.csv`: по каждой паре (правило, сторона) — лучшая на фите конфигурация в каждой группе
/// семейств (`fixed` = tp+trail, `atr` = tpa+tra, и каждое из четырёх) с показателями на фите; `chosen = 1` — совпала
/// с конфигурацией, замороженной для пары (лучшей из всех 140). Группа без конфигураций с определённой границей — без строки.
fn write_groups_fit(
    a: &VerdictArgs,
    vp: &VParams,
    grid: &[xg::Cfg],
    t: &XTable,
    res: &[XNet],
    chosen: &[(Rule, i8, usize)],
    groups: &[[Option<usize>; NGROUP]],
) -> Result<()> {
    let mut gl = vec![format!(
        "fam,wi,ti,theta,w_min,sign,group,{CFG_COLS},n,gross_mean,fund_mean,hold_mean,net_main,net_lo,net_hi,b_valid,chosen"
    )];
    for ((rule, sign, ch), gr) in chosen.iter().zip(groups) {
        for ((name, _), cell) in GROUPS.iter().zip(gr) {
            let Some(c) = *cell else { continue };
            let (k, r) = (t.keys[c], &res[c]);
            gl.push(
                [
                    rule.fam.to_string(),
                    rule.wi.to_string(),
                    rule.ti.to_string(),
                    vp.theta(rule.ti).to_string(),
                    vp.window(rule.wi).to_string(),
                    sign.to_string(),
                    (*name).to_string(),
                    cfg_cols(grid, k.cfg),
                    r.n.to_string(),
                    f6(r.gross),
                    f6(r.fund),
                    f6(r.hold),
                    f6(r.main),
                    f6(r.lo),
                    f6(r.hi),
                    r.valid.to_string(),
                    u8::from(c == *ch).to_string(),
                ]
                .join(","),
            );
        }
    }
    write_lines(&a.out.join("exits-verdict-fit-groups.csv"), &gl)
}

// ---------- стадия exits-test ----------

/// Замороженная пара из `exits-k.json`.
#[derive(Debug, Clone, Copy)]
struct Frozen {
    rule: Rule,
    sign: i8,
    cfg: u8,
    /// Лучшие на фите конфигурации по группам `GROUPS` (`best_by_group` в `exits-k.json`); `None` — в файле нет поля.
    best: Option<[Option<u8>; NGROUP]>,
}

/// `best_by_group` элемента `exits-k.json`: объект «группа → номер конфигурации | null». Номер должен лежать в сетке и
/// принадлежать семейству группы (сетка могла смениться между стадиями). Нет поля или `null` — `None`.
fn read_best_by_group(
    o: &serde_json::Value,
    i: usize,
    grid: &[xg::Cfg],
) -> Result<Option<[Option<u8>; NGROUP]>> {
    let m = match o.get("best_by_group") {
        None | Some(serde_json::Value::Null) => return Ok(None),
        Some(v) => v
            .as_object()
            .with_context(|| format!("exits-k.json[{i}].best_by_group: ожидался объект"))?,
    };
    let mut best = [None; NGROUP];
    for (slot, (name, fams)) in best.iter_mut().zip(GROUPS.iter()) {
        let Some(x) = m.get(*name).filter(|x| !x.is_null()) else {
            continue;
        };
        let cfg = x
            .as_u64()
            .and_then(|c| u8::try_from(c).ok())
            .filter(|c| usize::from(*c) < xg::NCFG)
            .with_context(|| format!("exits-k.json[{i}].best_by_group.{name}: вне сетки"))?;
        if !fams.contains(&grid[usize::from(cfg)].family) {
            bail!("exits-k.json[{i}].best_by_group.{name}: cfg {cfg} не из этой группы семейств");
        }
        *slot = Some(cfg);
    }
    Ok(Some(best))
}

fn read_exits_k(path: &Path, vp: &VParams, grid: &[xg::Cfg]) -> Result<Vec<Frozen>> {
    let text = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    let v: serde_json::Value = serde_json::from_str(&text).context("exits-k.json")?;
    let arr = v.as_array().context("exits-k.json: ожидался массив")?;
    let mut out: Vec<Frozen> = Vec::new();
    for (i, o) in arr.iter().enumerate() {
        let u = |name: &str| -> Result<u64> {
            o.get(name)
                .and_then(serde_json::Value::as_u64)
                .with_context(|| format!("exits-k.json[{i}].{name}"))
        };
        let small = |name: &str| -> Result<u8> {
            u8::try_from(u(name)?)
                .map_err(|_| anyhow!("exits-k.json[{i}].{name}: не помещается в u8"))
        };
        let rule = Rule {
            fam: small("fam")?,
            wi: small("wi")?,
            ti: small("ti")?,
        };
        vp.check_rule(rule)?;
        let sign = match o.get("sign").and_then(serde_json::Value::as_i64) {
            Some(1) => 1i8,
            Some(-1) => -1i8,
            other => bail!("exits-k.json[{i}].sign = {other:?}, ожидалось ±1"),
        };
        let cfg = small("cfg")?;
        if usize::from(cfg) >= xg::NCFG {
            bail!("exits-k.json[{i}].cfg = {cfg} вне сетки");
        }
        // конфигурация в файле — та же, что в коде (сетка могла смениться между стадиями)
        let g = &grid[usize::from(cfg)];
        let cap_ok = o.get("cap").and_then(serde_json::Value::as_u64) == Some(u64::from(g.cap()));
        let fam_ok =
            o.get("exit_family").and_then(serde_json::Value::as_str) == Some(g.family.name());
        if !(cap_ok && fam_ok) {
            bail!("exits-k.json[{i}]: cfg {cfg} не совпадает с сеткой кода (cap/exit_family)");
        }
        if out.iter().any(|f| f.rule == rule && f.sign == sign) {
            bail!("exits-k.json[{i}]: повтор пары (правило, сторона)");
        }
        let best = read_best_by_group(o, i, grid)?;
        out.push(Frozen {
            rule,
            sign,
            cfg,
            best,
        });
    }
    if out.is_empty() || out.len() > vp.trials.test_net_m {
        bail!(
            "exits-k.json: пар {}, ожидалось 1..={} (trials.test_net_m)",
            out.len(),
            vp.trials.test_net_m
        );
    }
    Ok(out)
}

/// Итог по замороженной паре на тесте.
struct TestRow {
    fz: Frozen,
    cell: usize,
    p_holm: f64,
    months_pos: usize,
    months_def: usize,
    months_total: usize,
    lo_ok: bool,
    p_ok: bool,
    months_ok: bool,
}

impl TestRow {
    fn passed(&self) -> usize {
        [self.lo_ok, self.p_ok, self.months_ok]
            .iter()
            .filter(|x| **x)
            .count()
    }

    fn found(&self) -> bool {
        self.passed() == 3
    }

    fn fails(&self) -> Vec<&'static str> {
        let mut v = Vec::new();
        if !self.lo_ok {
            v.push("нижняя граница net <= 0");
        }
        if !self.p_ok {
            v.push("p-Холм(net) >= alpha");
        }
        if !self.months_ok {
            v.push("net > 0 менее чем в 2/3 месяцев");
        }
        v
    }
}

/// Доли причин выхода и потолка для описательных таблиц.
struct Shares {
    tp: f64,
    sl: f64,
    tr: f64,
    cap: f64,
    mfe_mean: f64,
    mfe_min_mean: f64,
    taken: f64,
    taken_cap: f64,
}

fn shares(t: &XTable, c: usize) -> Shares {
    let a = &t.tot[c];
    let n = a[V_N];
    Shares {
        tp: ratio(a[V_TP], n),
        sl: ratio(a[V_SL], n),
        tr: ratio(a[V_TR], n),
        cap: ratio(a[V_CAP], n),
        mfe_mean: ratio(a[V_MFE], n),
        mfe_min_mean: ratio(a[V_MFE_MIN], n),
        taken: ratio(a[V_GROSS], a[V_MFE]),
        taken_cap: ratio(a[V_GROSS], a[V_MFEC]),
    }
}

pub(super) fn test_stage(a: &VerdictArgs, vp: &VParams, sha: &str) -> Result<()> {
    let v = &vp.verdict;
    let grid = xg::grid();
    let kpath =
        a.k.as_ref()
            .context("--k (exits-k.json стадии exits-fit) обязателен для стадии exits-test")?;
    let frozen = read_exits_k(kpath, vp, &grid)?;
    check_grid(&a.input)?;
    let (rows, dropped) = guard_rows(read_exits(&a.input.join("exits-test.csv"))?, true, vp)?;
    let pairs: BTreeSet<(Rule, i8)> = frozen.iter().map(|f| (f.rule, f.sign)).collect();
    let mut want: Vec<XKey> = Vec::new();
    for (r, sign) in &pairs {
        for cfg in 0..xg::NCFG as u8 {
            want.push(XKey {
                fam: r.fam,
                wi: r.wi,
                ti: r.ti,
                sign: *sign,
                cfg,
            });
        }
    }
    let not_k = rows
        .iter()
        .filter(|r| !pairs.contains(&(r.key.rule(), r.key.sign)))
        .count();
    let test_months = period_months(&vp.split.test)?;
    let t = build_xtable(&rows, &want)?;
    let res = x_results(&t, vp, a.threads)?;

    let cells: Vec<usize> = frozen
        .iter()
        .map(|f| {
            pos(
                &t.keys,
                &XKey {
                    fam: f.rule.fam,
                    wi: f.rule.wi,
                    ti: f.rule.ti,
                    sign: f.sign,
                    cfg: f.cfg,
                },
                "ячейка",
            )
        })
        .collect::<Result<_>>()?;
    let ps: Vec<f64> = cells.iter().map(|&c| res[c].p).collect();
    let ph = holm(&ps, vp.trials.test_net_m);
    let out: Vec<TestRow> = frozen
        .iter()
        .zip(&cells)
        .zip(&ph)
        .map(|((fz, &cell), &p_holm)| {
            let (mp, md) = month_pos(&t, cell);
            TestRow {
                fz: *fz,
                cell,
                p_holm,
                months_pos: mp,
                months_def: md,
                months_total: test_months.len(),
                lo_ok: res[cell].lo > 0.0,
                p_ok: p_holm < v.holm_alpha,
                months_ok: share_ok(mp, test_months.len(), &v.min_months_share),
            }
        })
        .collect();

    write_test_csvs(a, vp, &grid, &t, &res, &out)?;
    let summary = found_summary(vp, &out);
    let report = build_report(&ReportIn {
        vp,
        sha,
        grid: &grid,
        t: &t,
        res: &res,
        out: &out,
        pairs: &pairs,
        test_months: &test_months,
        summary: &summary,
        dropped,
        not_k,
    });
    fs::write(a.out.join("report-exits-tables.md"), report)?;
    for l in &summary {
        println!("{l}");
    }
    Ok(())
}

const SHARE_COLS: &str =
    "share_tp,share_sl,share_trail,share_cap,mfe_mean,mfe_min_mean,mfe_taken,mfe_cap_taken";

fn share_vals(s: &Shares) -> String {
    [
        s.tp,
        s.sl,
        s.tr,
        s.cap,
        s.mfe_mean,
        s.mfe_min_mean,
        s.taken,
        s.taken_cap,
    ]
    .iter()
    .map(|x| f6(*x))
    .collect::<Vec<_>>()
    .join(",")
}

fn write_test_csvs(
    a: &VerdictArgs,
    vp: &VParams,
    grid: &[xg::Cfg],
    t: &XTable,
    res: &[XNet],
    out: &[TestRow],
) -> Result<()> {
    let fees = (2.0 * leg_fee_bps(true), ROUNDTRIP_FEES_BPS);
    let mut vl = vec![format!(
        "fam,wi,ti,theta,w_min,sign,{CFG_COLS},n,gross_mean,fund_mean,hold_mean,fee_main,fee_second,net_main,net_lo,net_hi,p,p_holm,net_second,net_second_lo,net_second_hi,breakeven_slip,months_pos,months_defined,months_total,{SHARE_COLS},found,fail"
    )];
    for r in out {
        let (n, k) = (&res[r.cell], t.keys[r.cell]);
        vl.push(
            [
                k.fam.to_string(),
                k.wi.to_string(),
                k.ti.to_string(),
                vp.theta(k.ti).to_string(),
                vp.window(k.wi).to_string(),
                k.sign.to_string(),
                cfg_cols(grid, k.cfg),
                n.n.to_string(),
                f6(n.gross),
                f6(n.fund),
                f6(n.hold),
                f6(fees.0),
                f6(fees.1),
                f6(n.main),
                f6(n.lo),
                f6(n.hi),
                f6(n.p),
                f6(r.p_holm),
                f6(n.second),
                f6(n.second_lo),
                f6(n.second_hi),
                f6(n.main),
                r.months_pos.to_string(),
                r.months_def.to_string(),
                r.months_total.to_string(),
                share_vals(&shares(t, r.cell)),
                u8::from(r.found()).to_string(),
                r.fails().join("; "),
            ]
            .join(","),
        );
    }
    write_lines(&a.out.join("exits-verdict-test.csv"), &vl)?;

    // описательно: все конфигурации каждой пары (без Холма; `frozen = 1` — выбранная на фите)
    let mut gl = vec![format!(
        "fam,wi,ti,theta,w_min,sign,{CFG_COLS},frozen,n,gross_mean,fund_mean,hold_mean,net_main,net_lo,net_hi,net_second,{SHARE_COLS}"
    )];
    for (c, k) in t.keys.iter().enumerate() {
        let n = &res[c];
        let frozen = out.iter().any(|r| r.cell == c);
        gl.push(
            [
                k.fam.to_string(),
                k.wi.to_string(),
                k.ti.to_string(),
                vp.theta(k.ti).to_string(),
                vp.window(k.wi).to_string(),
                k.sign.to_string(),
                cfg_cols(grid, k.cfg),
                u8::from(frozen).to_string(),
                n.n.to_string(),
                f6(n.gross),
                f6(n.fund),
                f6(n.hold),
                f6(n.main),
                f6(n.lo),
                f6(n.hi),
                f6(n.second),
                share_vals(&shares(t, c)),
            ]
            .join(","),
        );
    }
    write_lines(&a.out.join("exits-verdict-test-grid.csv"), &gl)?;

    // лучшая в семействе (выбрана на фите, здесь только оценка): fixed против atr и по четырём семействам
    let mut tl = vec![format!(
        "fam,wi,ti,theta,w_min,sign,group,{CFG_COLS},n,gross_mean,fund_mean,hold_mean,net_main,net_lo,net_hi,net_second"
    )];
    for (_, g, k, n) in group_cells(t, res, out) {
        tl.push(
            [
                k.fam.to_string(),
                k.wi.to_string(),
                k.ti.to_string(),
                vp.theta(k.ti).to_string(),
                vp.window(k.wi).to_string(),
                k.sign.to_string(),
                GROUPS[g].0.to_string(),
                cfg_cols(grid, k.cfg),
                n.n.to_string(),
                f6(n.gross),
                f6(n.fund),
                f6(n.hold),
                f6(n.main),
                f6(n.lo),
                f6(n.hi),
                f6(n.second),
            ]
            .join(","),
        );
    }
    write_lines(&a.out.join("exits-verdict-test-groups.csv"), &tl)
}

/// Ячейки «лучшая в семействе» замороженных пар на тесте: `(индекс ячейки, группа, ключ, показатели)`, по парам в
/// порядке `exits-k.json`, затем по группам `GROUPS`. Пары без `best_by_group` и пустые группы пропускаются.
fn group_cells<'a>(
    t: &'a XTable,
    res: &'a [XNet],
    out: &[TestRow],
) -> Vec<(usize, usize, XKey, &'a XNet)> {
    let mut v = Vec::new();
    for r in out {
        let Some(best) = r.fz.best else { continue };
        for (gi, cfg) in best.iter().enumerate() {
            let Some(cfg) = *cfg else { continue };
            let key = XKey {
                fam: r.fz.rule.fam,
                wi: r.fz.rule.wi,
                ti: r.fz.rule.ti,
                sign: r.fz.sign,
                cfg,
            };
            if let Ok(c) = pos(&t.keys, &key, "ячейка") {
                v.push((c, gi, key, &res[c]));
            }
        }
    }
    v
}

/// Строки итога по сторонам: «выход в прибыль найден» / «не доказан» с названием, что не сошлось.
fn found_summary(vp: &VParams, out: &[TestRow]) -> Vec<String> {
    let mut lines = Vec::new();
    for (sign, name) in [(1i8, "Пампы (sign +1)"), (-1i8, "Дампы (sign −1)")] {
        let rows: Vec<&TestRow> = out.iter().filter(|r| r.fz.sign == sign).collect();
        let ok: Vec<String> = rows
            .iter()
            .filter(|r| r.found())
            .map(|r| format!("{} → cfg {}", rule_label(vp, r.fz.rule), r.fz.cfg))
            .collect();
        if !ok.is_empty() {
            lines.push(format!(
                "{name}: выход в прибыль найден — пар, прошедших все условия П-35: {} ({}).",
                ok.len(),
                ok.join("; ")
            ));
            continue;
        }
        let mut s = format!("{name}: выход в прибыль не доказан");
        match rows
            .iter()
            .max_by(|a, b| a.passed().cmp(&b.passed()).then(b.fz.rule.cmp(&a.fz.rule)))
        {
            Some(n) => s.push_str(&format!(
                ". Ближайшая пара {} → cfg {} прошла {} из 3 условий, не сошлось: {}",
                rule_label(vp, n.fz.rule),
                n.fz.cfg,
                n.passed(),
                n.fails().join("; ")
            )),
            None => s.push_str(": нет пар"),
        }
        let cnt = |f: fn(&TestRow) -> bool| rows.iter().filter(|r| !f(r)).count();
        s.push_str(&format!(
            ". Не сошлось по парам (из {}): нижняя граница net {} · p-Холм net {} · месяцы net {}.",
            rows.len(),
            cnt(|r| r.lo_ok),
            cnt(|r| r.p_ok),
            cnt(|r| r.months_ok)
        ));
        lines.push(s);
    }
    lines
}

struct ReportIn<'a> {
    vp: &'a VParams,
    sha: &'a str,
    grid: &'a [xg::Cfg],
    t: &'a XTable,
    res: &'a [XNet],
    out: &'a [TestRow],
    pairs: &'a BTreeSet<(Rule, i8)>,
    test_months: &'a [i32],
    summary: &'a [String],
    dropped: usize,
    not_k: usize,
}

fn pct(x: f64) -> String {
    if x.is_nan() {
        "—".to_string()
    } else {
        format!("{:.0} %", x * 100.0)
    }
}

/// Раздел 3 отчёта: лучшая в семействе — fixed (tp, trail) против atr (tpa, tra), выбрана на фите, оценена на тесте.
fn groups_section(d: &mut Doc, r: &ReportIn) {
    let (vp, t) = (r.vp, r.t);
    d.line("## 3. Лучшая в семействе: bps против единиц ATR-прокси (выбрана на фите, оценена на тесте)");
    if r.out.iter().all(|o| o.fz.best.is_none()) {
        d.line("В `exits-k.json` нет `best_by_group` (файл записан версией без семейств в единицах ATR-прокси) — сводка не строится.");
        d.line("");
        return;
    }
    d.line("Для каждой пары конфигурация лучшей нижней границы в группе (`fixed` = tp+trail, `atr` = tpa+tra, а также каждое из четырёх семейств) выбрана на фите (`exits-verdict-fit-groups.csv`); здесь — её результат на тесте. Описательно, без Холма: это не новое испытание гипотезы, а ответ на вопрос «зависит ли лучший стоп/трейлинг от волатильности». Сделок у `atr`-групп меньше: сделки без определённой A в них не входят.");
    let cells = group_cells(t, r.res, r.out);
    for o in r.out {
        let mine: Vec<&(usize, usize, XKey, &XNet)> = cells
            .iter()
            .filter(|(_, _, k, _)| k.rule() == o.fz.rule && k.sign == o.fz.sign)
            .collect();
        if mine.is_empty() {
            continue;
        }
        d.line("");
        d.line(&format!(
            "### {} · {}",
            rule_label(vp, o.fz.rule),
            if o.fz.sign > 0 {
                "памп (+1)"
            } else {
                "дамп (−1)"
            }
        ));
        d.line(&md_row(&[
            "группа".into(),
            "cfg".into(),
            "выход".into(),
            "сделок".into(),
            "net [нижн.; верхн.]".into(),
            "удержание, мин".into(),
            "tp / sl / trail / cap".into(),
        ]));
        d.line(&md_sep(7));
        for (c, gi, k, n) in mine.iter().map(|x| **x) {
            let s = shares(t, c);
            d.line(&md_row(&[
                GROUPS[gi].0.to_string(),
                k.cfg.to_string(),
                cfg_label(r.grid, k.cfg),
                format!("{}", n.n),
                format!("{} [{}; {}]", r3(n.main), r3(n.lo), r3(n.hi)),
                r3(n.hold),
                format!(
                    "{} / {} / {} / {}",
                    pct(s.tp),
                    pct(s.sl),
                    pct(s.tr),
                    pct(s.cap)
                ),
            ]));
        }
        let by = |name: &str| {
            mine.iter()
                .find(|(_, gi, _, _)| GROUPS[*gi].0 == name)
                .map(|(_, _, _, n)| *n)
        };
        if let (Some(a), Some(f)) = (by("atr"), by("fixed")) {
            d.line(&format!(
                "atr − fixed: Δnet = {} bps (net {} против {}; сделок {} против {}).",
                r3(a.main - f.main),
                r3(a.main),
                r3(f.main),
                a.n,
                f.n
            ));
        }
    }
    d.line("");
}

fn build_report(r: &ReportIn) -> String {
    let (vp, t) = (r.vp, r.t);
    let v = &vp.verdict;
    let mut d = Doc(String::new());
    d.line("# П-35 · выходы в прибыль — таблицы (verdict --stage exits-test)");
    d.line("");
    d.line(&format!(
        "Параметры: sha256 `{}`; бутстрэп по суткам (все монеты вместе) B={}, seed={}, интервал {:.0} % = перцентили {:.1}/{:.1}; \
         Холм: net m={} (замороженных пар {}). Суток-кластеров {}, месяцев теста {} ({}). Конфигураций в сетке {}.",
        r.sha,
        v.bootstrap_b,
        v.seed,
        v.ci_level * 100.0,
        (1.0 - v.ci_level) * 50.0,
        100.0 - (1.0 - v.ci_level) * 50.0,
        vp.trials.test_net_m,
        r.out.len(),
        t.days,
        r.test_months.len(),
        r.test_months
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(", "),
        xg::NCFG
    ));
    d.line(&format!(
        "Строк `exits-test` вне замороженных пар (правило, сторона): {} (не использованы); вне периода теста отброшено: {}.",
        r.not_k, r.dropped
    ));
    d.line("");
    d.line("## Итог (П-35)");
    for l in r.summary {
        d.line(&format!("- {l}"));
    }
    d.line("");

    d.line("## 1. Замороженные пары: net на сделку, bps");
    d.line("net = среднее(gross − фандинг) − издержки; основной вариант — тейкер/тейкер `2·leg_fee_bps(true)`, второй — `ROUNDTRIP_FEES_BPS`. Проскальзывание не вычитается; безубыточное проскальзывание (bps на круг) = net до его вычета. Доля mfe — `Σ gross / Σ mfe` (потолок окна 1440 мин / окна собственного cap).");
    d.line(&md_row(&[
        "правило".into(),
        "знак".into(),
        "выход".into(),
        "сделок".into(),
        "gross".into(),
        "фандинг".into(),
        "удержание, мин".into(),
        "net осн. [95 %]".into(),
        "p-Холм".into(),
        "net втор. [95 %]".into(),
        "безуб. проскальз.".into(),
        "мес. net>0".into(),
        "причины tp/sl/trail/cap".into(),
        "забрано mfe".into(),
        "найден".into(),
    ]));
    d.line(&md_sep(15));
    for o in r.out {
        let (n, s) = (&r.res[o.cell], shares(t, o.cell));
        d.line(&md_row(&[
            rule_label(vp, o.fz.rule),
            format!("{:+}", o.fz.sign),
            cfg_label(r.grid, o.fz.cfg),
            format!("{}", n.n),
            r3(n.gross),
            r3(n.fund),
            r3(n.hold),
            format!("{} [{}; {}]", r3(n.main), r3(n.lo), r3(n.hi)),
            r3(o.p_holm),
            format!(
                "{} [{}; {}]",
                r3(n.second),
                r3(n.second_lo),
                r3(n.second_hi)
            ),
            r3(n.main),
            format!("{}/{}", o.months_pos, o.months_total),
            format!(
                "{} / {} / {} / {}",
                pct(s.tp),
                pct(s.sl),
                pct(s.tr),
                pct(s.cap)
            ),
            format!("{} / {}", pct(s.taken), pct(s.taken_cap)),
            if o.found() { "да" } else { "нет" }.to_string(),
        ]));
    }
    d.line("");

    d.line("## 2. Все конфигурации на тесте (описательно, без Холма; ★ — выбранная на фите)");
    d.line(&format!("Чтобы было видно, не случайность ли выбор: net и нижняя граница каждой из {} конфигураций по каждой паре (84 в bps и 56 в единицах ATR-прокси; у последних «сделок» меньше — A определена не у всех сделок). Показатели mfe — средний потолок задним числом, bps (окно 1440 мин) и минута его достижения от входа.", xg::NCFG));
    for (rule, sign) in r.pairs {
        d.line("");
        d.line(&format!(
            "### {} · {}",
            rule_label(vp, *rule),
            if *sign > 0 {
                "памп (+1)"
            } else {
                "дамп (−1)"
            }
        ));
        d.line(&md_row(&[
            "cfg".into(),
            "выход".into(),
            "сделок".into(),
            "net [нижн.; верхн.]".into(),
            "удержание, мин".into(),
            "tp / sl / trail / cap".into(),
            "mfe, bps (мин)".into(),
            "забрано mfe".into(),
        ]));
        d.line(&md_sep(8));
        for (c, k) in t.keys.iter().enumerate() {
            if k.rule() != *rule || k.sign != *sign {
                continue;
            }
            let (n, s) = (&r.res[c], shares(t, c));
            let star = if r.out.iter().any(|o| o.cell == c) {
                " ★"
            } else {
                ""
            };
            d.line(&md_row(&[
                format!("{}{star}", k.cfg),
                cfg_label(r.grid, k.cfg),
                format!("{}", n.n),
                format!("{} [{}; {}]", r3(n.main), r3(n.lo), r3(n.hi)),
                r3(n.hold),
                format!(
                    "{} / {} / {} / {}",
                    pct(s.tp),
                    pct(s.sl),
                    pct(s.tr),
                    pct(s.cap)
                ),
                format!("{} ({})", r3(s.mfe_mean), r3(s.mfe_min_mean)),
                pct(s.taken),
            ]));
        }
    }
    d.line("");

    groups_section(&mut d, r);

    d.line("## Оговорки");
    d.line(&format!(
        "- Число испытаний: подбор — {} конфигураций × пары на фите (в m теста не входит); тест — {} замороженных пар, Холм по m={}; расширений сетки после просмотра нет.",
        xg::NCFG,
        r.out.len(),
        vp.trials.test_net_m
    ));
    d.line("- Единица A — close-to-close прокси ATR (`sigma_h_bps`), настоящего ATR нет: в кэше нет high/low. Семейства `tpa`/`tra` считаются только по сделкам с определённой A, поэтому «bps против ATR» сравнивается на разных наборах сделок (колонка «сделок»); число исключённых — `atr_undefined` в `run-meta.json`.");
    d.line("- Реплика бутстрэпа с неопределённой статистикой не входит в границы интервала и считается «не отвергла нуль» в p.");
    d.line("- Месяцы теста: знаменатель долей — все месяцы периода `split.test`; месяц без сделок пары не считается «net > 0».");
    d.line("- Выход решается по close минуты и исполняется по c[k+1] (задержка в одну минуту), предел `cap` — по c[k] в запланированную минуту; внутриминутные касания не моделируются. `mfe` — потолок задним числом, не стратегия.");
    d.line("- Набор сделок по `cap` совпадает с `nret` из `daycells` для H = cap (допущение 2 `exits.rs`): сделка без покрытия фандинга окна до cap в конфигурацию не входит.");
    d.0
}
