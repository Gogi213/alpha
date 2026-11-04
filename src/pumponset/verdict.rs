//! `alpha pumponset verdict` — вердикты по таблицам `run` (спека: docs/findings/pumponset-run-spec-2026-11-05.md,
//! протокол П-34 §5–§8). Читает только csv и файл параметров; издержки — `crate::lob::costs`.
//!
//! Стадия `fit`: ранг правил (нижняя граница 95 % lift точности при `verdict.rank.H_min`, среднее по двум знакам),
//! лучшее правило семейства, `K` лучших семейств → `k.json` и строка для `JOURNAL.md`.
//! Стадия `test`: на правила из `k.json` — точность против базы C2a, lift с интервалом, Холм, вердикт
//! (подтверждено / оспорено / не различимо), net после издержек и фандинга, итог «встать рано можно / не доказано»
//! (§7.6), полнота по лагам, «какой признак первым» как разность долей эпизодов и C2b.
//!
//! # Допущения (то, чего протокол не называет)
//! 1. Ранг определён, только если у правила есть данные в обоих знаках (оба lift и обе нижние границы конечны);
//!    иначе правило «без ранга» и в отбор не идёт (счётчик — в строке журнала).
//! 2. Ничья ранга — меньший θ, затем меньший w (протокол), затем меньшее семейство/индекс; сравнение ранга точное.
//! 3. «Тот же знак» в вердикте = та же ячейка (правило, знак, H) в фите и в тесте: обе нижние границы > 1.
//! 4. Доля месяцев теста с lift > 1: числитель — месяцы, где lift ячейки определён и > 1; знаменатель — ВСЕ месяцы
//!    периода `split.test` (2026-01…2026-10 — 10), а не только присутствующие в `daycells-test.csv`: месяц без данных
//!    ячейки (или совсем без строк) — не «lift > 1». То же для net > 0 (месяц без сделок — не «net > 0»).
//! 5. Net: интервал и p — как у lift (нижняя граница 95 % = перцентиль 2,5; односторонний p по рекурсии «net ≤ 0»);
//!    Холм по `trials.test_net_m` только на основных ячейках (`verdict.net.H_min`), прочие H — описательно.
//! 6. «Встать рано можно» (§7.6) для (правило, знак) при основном H: lift-вердикт «подтверждено» (включая фит) И
//!    нижняя граница net > 0 И p-Холм(net) < α И net > 0 не менее чем в `min_months_share` месяцев теста
//!    (по аналогии с lift: интервал и Холм вместе). Пампы и дампы — отдельные итоги.
//! 7. Кластеры — сутки всех монет в файле стадии (объединение суток по всем строкам файла, до отбора правил).
//!    Чувствительность «монета × сутки» из `daycells` не восстановима и не считалась.
//! 8. Квантили лага p10/p50/p90 по месяцам объединить нельзя — они остаются в `lags-<stage>.csv` помесячно;
//!    в сводках — только суммы счётчиков. Сводка «сколько хода прошло» — взвешенное по `n_fired` среднее
//!    ПОМЕСЯЧНЫХ медиан (приближение, помечено так в таблицах).
//! 9. Месяцы вне `split.<стадия>`: фит — отказ при любом месяце вне диапазона (защита от утечки теста в ранг);
//!    тест — месяц раньше начала = отказ, позже конца = строки отбрасываются (число — в отчёт).

#![allow(
    // численное ядро: индексы ограничены проверенными длинами; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::stats::{bootstrap, holm, interval, Sparse};
use super::types::Rule;
use crate::lob::costs::{leg_fee_bps, ROUNDTRIP_FEES_BPS};
use anyhow::{anyhow, bail, Context, Result};
use clap::{Args, ValueEnum};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::BTreeMap;
use std::fmt::Display;
use std::fs;
use std::path::{Path, PathBuf};
use std::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum Stage {
    Fit,
    Test,
}

impl Stage {
    fn name(self) -> &'static str {
        match self {
            Stage::Fit => "fit",
            Stage::Test => "test",
        }
    }
}

#[derive(Debug, Args)]
pub struct VerdictArgs {
    /// `fit` — ранг и отбор K; `test` — вердикты на замороженных правилах.
    #[arg(long, value_enum)]
    pub stage: Stage,
    /// Каталог csv, записанных `run` для этой стадии (`daycells-<stage>.csv`, `lags-…`, `first-…`, `progress-…`).
    #[arg(long = "in")]
    pub input: PathBuf,
    /// Каталог с `daycells-fit.csv` для стадии `test` (по умолчанию — `--in`).
    #[arg(long = "in-fit")]
    pub in_fit: Option<PathBuf>,
    /// Файл параметров (`docs/research/P-34-params.json`).
    #[arg(long)]
    pub params: PathBuf,
    /// Каталог результатов.
    #[arg(long)]
    pub out: PathBuf,
    /// `k.json` стадии `fit` (для стадии `test`).
    #[arg(long)]
    pub k: Option<PathBuf>,
    /// Число потоков бутстрэпа (на результат не влияет).
    #[arg(long, default_value_t = 1)]
    pub threads: usize,
}

// ---------- параметры (секции verdict / trials / costs читаются здесь, остальное — нужное вердикту) ----------

#[derive(Debug, Clone, Deserialize)]
pub(super) struct VParams {
    features: VFeatures,
    label_forward: VLabel,
    verdict: VVerdict,
    trials: VTrials,
    costs: VCosts,
    split: VSplit,
}

#[derive(Debug, Clone, Deserialize)]
struct VFeatures {
    windows_min: Vec<u32>,
    theta: Vec<f64>,
}

#[derive(Debug, Clone, Deserialize)]
struct VLabel {
    #[serde(rename = "H_min")]
    h_min: Vec<u32>,
}

#[derive(Debug, Clone, Deserialize)]
struct VVerdict {
    ci_level: f64,
    #[serde(rename = "bootstrap_B")]
    bootstrap_b: usize,
    seed: u64,
    cluster: String,
    holm_alpha: f64,
    min_months_share: VShare,
    #[serde(rename = "K")]
    k: usize,
    rank: VRank,
    families: usize,
    net: VNet,
    recall_delta_min: Vec<i64>,
}

#[derive(Debug, Clone, Deserialize)]
pub(super) struct VShare {
    pub(super) num: usize,
    pub(super) den: usize,
}

#[derive(Debug, Clone, Deserialize)]
struct VRank {
    #[serde(rename = "H_min")]
    h_min: u32,
    average_over_signs: bool,
}

#[derive(Debug, Clone, Deserialize)]
struct VNet {
    #[serde(rename = "H_min")]
    h_min: u32,
    #[serde(rename = "descriptive_H_min")]
    descriptive_h_min: Vec<u32>,
}

#[derive(Debug, Clone, Deserialize)]
struct VTrials {
    fit_total: usize,
    test_lift_m: usize,
    test_net_m: usize,
}

#[derive(Debug, Clone, Deserialize)]
struct VCosts {
    main: String,
    second: String,
}

#[derive(Debug, Clone, Deserialize)]
struct VSplit {
    fit: [String; 2],
    test: [String; 2],
}

impl VParams {
    pub(super) fn parse(text: &str) -> Result<VParams> {
        let p: VParams = serde_json::from_str(text).context("params.json")?;
        let v = &p.verdict;
        if p.features.windows_min.is_empty()
            || p.features.theta.is_empty()
            || p.label_forward.h_min.is_empty()
        {
            bail!("params: windows_min, theta, H_min непусты");
        }
        let open01 = |x: f64| x > 0.0 && x < 1.0;
        if !open01(v.ci_level) || !open01(v.holm_alpha) {
            bail!("params: verdict.ci_level и holm_alpha в (0,1)");
        }
        if v.bootstrap_b == 0 || v.k == 0 || v.families == 0 {
            bail!("params: verdict.bootstrap_B, K, families > 0");
        }
        if v.min_months_share.den == 0 || v.min_months_share.num > v.min_months_share.den {
            bail!("params: verdict.min_months_share: 0 <= num <= den, den > 0");
        }
        if v.cluster != "day_all_symbols" {
            bail!(
                "params: verdict.cluster = {}: реализован только day_all_symbols",
                v.cluster
            );
        }
        if !v.rank.average_over_signs {
            bail!("params: verdict.rank.average_over_signs = false не реализовано (ранг — среднее по двум знакам)");
        }
        if v.recall_delta_min != [0, 30, 60] {
            bail!("params: verdict.recall_delta_min должен быть [0,30,60] (колонки n_le0/n_le30/n_le60 в lags)");
        }
        if p.costs.main != "2*leg_fee_bps(true)" || p.costs.second != "ROUNDTRIP_FEES_BPS" {
            bail!(
                "params: costs.main/second: реализованы 2*leg_fee_bps(true) и ROUNDTRIP_FEES_BPS"
            );
        }
        p.hi_of(v.rank.h_min)?;
        p.hi_of(v.net.h_min)?;
        for &h in &v.net.descriptive_h_min {
            p.hi_of(h)?;
        }
        Ok(p)
    }

    /// Индекс `hi` горизонта `h` в `label_forward.H_min`.
    fn hi_of(&self, h: u32) -> Result<usize> {
        self.label_forward
            .h_min
            .iter()
            .position(|&x| x == h)
            .ok_or_else(|| anyhow!("H = {h} нет в label_forward.H_min"))
    }

    fn theta(&self, ti: u8) -> f64 {
        self.features
            .theta
            .get(usize::from(ti))
            .copied()
            .unwrap_or(f64::NAN)
    }

    fn window(&self, wi: u8) -> u32 {
        self.features
            .windows_min
            .get(usize::from(wi))
            .copied()
            .unwrap_or(0)
    }

    fn h_min(&self, hi: u8) -> u32 {
        self.label_forward
            .h_min
            .get(usize::from(hi))
            .copied()
            .unwrap_or(0)
    }

    fn check_rule(&self, r: Rule) -> Result<()> {
        if usize::from(r.fam) >= self.verdict.families
            || usize::from(r.wi) >= self.features.windows_min.len()
            || usize::from(r.ti) >= self.features.theta.len()
        {
            bail!(
                "правило fam={} wi={} ti={} вне параметров (семейств {}, окон {}, порогов {})",
                r.fam,
                r.wi,
                r.ti,
                self.verdict.families,
                self.features.windows_min.len(),
                self.features.theta.len()
            );
        }
        Ok(())
    }
}

const FAM_NAMES: [&str; 11] = [
    "F1", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "S_oi", "S_all",
];

fn fam_name(f: u8) -> String {
    FAM_NAMES
        .get(usize::from(f))
        .map_or_else(|| format!("fam{f}"), |s| (*s).to_string())
}

fn rule_label(vp: &VParams, r: Rule) -> String {
    format!(
        "{} w{} θ{}",
        fam_name(r.fam),
        vp.window(r.wi),
        vp.theta(r.ti)
    )
}

// ---------- числа и текст ----------

/// Число в csv: `NaN` — пусто, ±∞ — `inf`/`-inf`, иначе 6 знаков.
fn f6(x: f64) -> String {
    if x.is_nan() {
        String::new()
    } else if x.is_infinite() {
        (if x > 0.0 { "inf" } else { "-inf" }).to_string()
    } else {
        format!("{x:.6}")
    }
}

/// Число в таблицах отчёта: `NaN` — «—», ±∞ — «∞».
fn r3(x: f64) -> String {
    if x.is_nan() {
        "—".to_string()
    } else if x.is_infinite() {
        (if x > 0.0 { "∞" } else { "-∞" }).to_string()
    } else {
        format!("{x:.3}")
    }
}

fn ratio(a: f64, b: f64) -> f64 {
    if b > 0.0 {
        a / b
    } else {
        f64::NAN
    }
}

fn write_lines(path: &Path, lines: &[String]) -> Result<()> {
    let mut s = lines.join("\n");
    s.push('\n');
    fs::write(path, s).with_context(|| path.display().to_string())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

struct Doc(String);

impl Doc {
    fn line(&mut self, s: &str) {
        self.0.push_str(s);
        self.0.push('\n');
    }
}

fn md_sep(n: usize) -> String {
    format!("|{}", "---|".repeat(n))
}

fn md_row(cells: &[String]) -> String {
    format!("| {} |", cells.join(" | "))
}

// ---------- чтение csv ----------

fn read_csv(path: &Path, head: &[&str]) -> Result<Vec<csv::StringRecord>> {
    let mut rd = csv::ReaderBuilder::new()
        .trim(csv::Trim::All)
        .from_path(path)
        .with_context(|| path.display().to_string())?;
    let h = rd.headers()?.clone();
    let got: Vec<&str> = h.iter().collect();
    if got != head {
        bail!(
            "{}: заголовок {:?}, ожидался {:?}",
            path.display(),
            got,
            head
        );
    }
    let mut out = Vec::new();
    for (i, rec) in rd.records().enumerate() {
        out.push(rec.with_context(|| format!("{} строка {}", path.display(), i + 2))?);
    }
    Ok(out)
}

fn field<T>(rec: &csv::StringRecord, i: usize, name: &str) -> Result<T>
where
    T: FromStr,
    T::Err: Display,
{
    let s = rec.get(i).unwrap_or("");
    s.parse::<T>()
        .map_err(|e| anyhow!("поле {name} = '{s}': {e}"))
}

fn opt_f64(rec: &csv::StringRecord, i: usize, name: &str) -> Result<Option<f64>> {
    match rec.get(i).unwrap_or("") {
        "" => Ok(None),
        _ => Ok(Some(field::<f64>(rec, i, name)?)),
    }
}

const DAYCELLS_HEAD: [&str; 14] = [
    "fam",
    "wi",
    "ti",
    "sign",
    "hi",
    "day",
    "month",
    "n",
    "hit",
    "bn",
    "bhit",
    "nret",
    "gross_bps",
    "fund_bps",
];
const LAGS_HEAD: [&str; 17] = [
    "kind", "stratum", "fam", "wi", "ti", "sign", "di", "month", "n_ep", "n_fired", "n_lt0",
    "n_le0", "n_le30", "n_le60", "p10", "p50", "p90",
];
const FIRST_HEAD: [&str; 9] = [
    "kind", "stratum", "wi", "ti", "sign", "di", "fam", "n_ep", "n_first",
];
const PROGRESS_HEAD: [&str; 17] = [
    "stratum",
    "fam",
    "wi",
    "ti",
    "sign",
    "di",
    "month",
    "n_fired",
    "done_bps_p10",
    "done_bps_p50",
    "done_bps_p90",
    "left_share_p10",
    "left_share_p50",
    "left_share_p90",
    "to_peak_bps_p10",
    "to_peak_bps_p50",
    "to_peak_bps_p90",
];
const KINDS: [&str; 2] = ["ep", "c2b"];
const STRATA: [&str; 5] = ["all", "market", "nonmarket", "outlier", "wick"];

fn idx_of(list: &[&str], s: &str, what: &str) -> Result<u8> {
    list.iter()
        .position(|x| *x == s)
        .map(|i| i as u8)
        .ok_or_else(|| anyhow!("{what} = '{s}' неизвестно"))
}

fn sign_of(rec: &csv::StringRecord, i: usize) -> Result<i8> {
    let s: i8 = field(rec, i, "sign")?;
    if s != 1 && s != -1 {
        bail!("sign = {s}, ожидалось ±1");
    }
    Ok(s)
}

// ---------- суточные ячейки ----------

/// Ячейка: правило, знак, горизонт. Порядок — как в csv (`fam,wi,ti,sign,hi`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Key {
    fam: u8,
    wi: u8,
    ti: u8,
    sign: i8,
    hi: u8,
}

impl Key {
    fn rule(self) -> Rule {
        Rule {
            fam: self.fam,
            wi: self.wi,
            ti: self.ti,
        }
    }
}

#[derive(Debug, Clone)]
struct DayRow {
    key: Key,
    day: i64,
    month: i32,
    n: f64,
    hit: f64,
    bn: f64,
    bhit: f64,
    nret: f64,
    gross: f64,
    fund: f64,
}

fn parse_daycell(rec: &csv::StringRecord) -> Result<DayRow> {
    let key = Key {
        fam: field(rec, 0, "fam")?,
        wi: field(rec, 1, "wi")?,
        ti: field(rec, 2, "ti")?,
        sign: sign_of(rec, 3)?,
        hi: field(rec, 4, "hi")?,
    };
    let (n, hit, bn, bhit, nret): (u64, u64, u64, u64, u64) = (
        field(rec, 7, "n")?,
        field(rec, 8, "hit")?,
        field(rec, 9, "bn")?,
        field(rec, 10, "bhit")?,
        field(rec, 11, "nret")?,
    );
    if hit > n || bhit > bn {
        bail!("hit > n или bhit > bn");
    }
    let (gross, fund): (f64, f64) = (field(rec, 12, "gross_bps")?, field(rec, 13, "fund_bps")?);
    if !gross.is_finite() || !fund.is_finite() {
        bail!("gross_bps/fund_bps не конечны");
    }
    Ok(DayRow {
        key,
        day: field(rec, 5, "day")?,
        month: field(rec, 6, "month")?,
        n: n as f64,
        hit: hit as f64,
        bn: bn as f64,
        bhit: bhit as f64,
        nret: nret as f64,
        gross,
        fund,
    })
}

fn read_daycells(path: &Path) -> Result<Vec<DayRow>> {
    let recs = read_csv(path, &DAYCELLS_HEAD)?;
    let mut out = Vec::with_capacity(recs.len());
    for (i, rec) in recs.iter().enumerate() {
        out.push(
            parse_daycell(rec).with_context(|| format!("{} строка {}", path.display(), i + 2))?,
        );
    }
    Ok(out)
}

/// `гггг-мм` → `ггггмм`.
fn month_of(s: &str) -> Result<i32> {
    let (y, m) = s
        .split_once('-')
        .with_context(|| format!("месяц '{s}': ожидалось гггг-мм"))?;
    let (y, m): (i32, i32) = (y.trim().parse()?, m.trim().parse()?);
    if !(1..=12).contains(&m) {
        bail!("месяц '{s}': номер месяца 1..12");
    }
    Ok(y * 100 + m)
}

/// Все месяцы периода `["гггг-мм", "гггг-мм"]` (оба включительно) как `ггггмм` по возрастанию (допущение 4).
fn period_months(r: &[String; 2]) -> Result<Vec<i32>> {
    let (lo, hi) = (month_of(&r[0])?, month_of(&r[1])?);
    if lo > hi {
        bail!("период {r:?}: начало позже конца");
    }
    let mut out = Vec::new();
    let (mut y, mut m) = (lo / 100, lo % 100);
    while y * 100 + m <= hi {
        out.push(y * 100 + m);
        m += 1;
        if m == 13 {
            y += 1;
            m = 1;
        }
    }
    Ok(out)
}

/// Допущение 9: диапазон месяцев стадии. Возвращает строки в диапазоне и число отброшенных (только тест, позже конца).
fn guard_months(rows: Vec<DayRow>, stage: Stage, vp: &VParams) -> Result<(Vec<DayRow>, usize)> {
    let r = match stage {
        Stage::Fit => &vp.split.fit,
        Stage::Test => &vp.split.test,
    };
    let (lo, hi) = (month_of(&r[0])?, month_of(&r[1])?);
    let mut kept = Vec::with_capacity(rows.len());
    let mut dropped = 0usize;
    for row in rows {
        if row.month < lo || (row.month > hi && stage == Stage::Fit) {
            bail!(
                "месяц {} вне split.{} [{}…{}]: стадия {} читает только свой период",
                row.month,
                stage.name(),
                lo,
                hi,
                stage.name()
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

/// Суточные таблицы стадии в разреженном виде для бутстрэпа и помесячные суммы.
struct Table {
    keys: Vec<Key>,
    /// Число суток-кластеров (объединение суток всех строк файла).
    days: usize,
    months: Vec<i32>,
    /// `[n, hit, bn, bhit]`.
    lift: Sparse<4>,
    /// `[nret, gross, fund]`.
    net: Sparse<3>,
    /// `[ячейка][месяц]` → `[n, hit, bn, bhit, nret, gross, fund]`.
    monthly: Vec<Vec<[f64; 7]>>,
}

fn pos<T: Ord + std::fmt::Debug>(v: &[T], x: &T, what: &str) -> Result<usize> {
    v.binary_search(x)
        .map_err(|_| anyhow!("{what} {x:?}: нет в списке"))
}

/// `want` — набор ячеек, который надо вернуть (даже пустыми); `None` — все ячейки, найденные в строках.
/// Кластеры и месяцы берутся по ВСЕМ строкам (до отбора по `want`).
fn build_table(rows: &[DayRow], want: Option<&[Key]>) -> Result<Table> {
    let mut days: Vec<i64> = rows.iter().map(|r| r.day).collect();
    days.sort_unstable();
    days.dedup();
    let mut months: Vec<i32> = rows.iter().map(|r| r.month).collect();
    months.sort_unstable();
    months.dedup();
    let want: Option<Vec<Key>> = want.map(|w| {
        let mut w = w.to_vec();
        w.sort_unstable();
        w.dedup();
        w
    });
    let mut sel: Vec<&DayRow> = match &want {
        Some(w) => rows
            .iter()
            .filter(|r| w.binary_search(&r.key).is_ok())
            .collect(),
        None => rows.iter().collect(),
    };
    sel.sort_by(|a, b| a.key.cmp(&b.key).then(a.day.cmp(&b.day)));
    for w in sel.windows(2) {
        if w[0].key == w[1].key && w[0].day == w[1].day {
            bail!(
                "повтор строки: fam={} wi={} ti={} sign={} hi={} day={}",
                w[0].key.fam,
                w[0].key.wi,
                w[0].key.ti,
                w[0].key.sign,
                w[0].key.hi,
                w[0].day
            );
        }
    }
    let keys: Vec<Key> = match want {
        Some(w) => w,
        None => {
            let mut k: Vec<Key> = sel.iter().map(|r| r.key).collect();
            k.dedup();
            k
        }
    };
    let mut lift = Sparse::<4>::new();
    let mut net = Sparse::<3>::new();
    let mut monthly = Vec::with_capacity(keys.len());
    let mut i = 0usize;
    for key in &keys {
        let mut mm = vec![[0.0f64; 7]; months.len()];
        while i < sel.len() && sel[i].key == *key {
            let r = sel[i];
            let di = pos(&days, &r.day, "сутки")?;
            lift.push(di as u32, [r.n, r.hit, r.bn, r.bhit]);
            net.push(di as u32, [r.nret, r.gross, r.fund]);
            let m = &mut mm[pos(&months, &r.month, "месяц")?];
            for (a, b) in m
                .iter_mut()
                .zip([r.n, r.hit, r.bn, r.bhit, r.nret, r.gross, r.fund])
            {
                *a += b;
            }
            i += 1;
        }
        lift.end_cell();
        net.end_cell();
        monthly.push(mm);
    }
    Ok(Table {
        keys,
        days: days.len(),
        months,
        lift,
        net,
        monthly,
    })
}

// ---------- lift ----------

/// Lift = точность срабатываний / база C2a. `NaN` — нет срабатываний или моментов базы или обе доли нулевые;
/// `+∞` — база нулевая при ненулевой точности.
fn lift_of(n: f64, hit: f64, bn: f64, bhit: f64) -> f64 {
    if !(n > 0.0 && bn > 0.0) {
        return f64::NAN;
    }
    let (pr, ba) = (hit / n, bhit / bn);
    if ba > 0.0 {
        pr / ba
    } else if pr > 0.0 {
        f64::INFINITY
    } else {
        f64::NAN
    }
}

#[derive(Debug, Clone, Copy)]
struct LiftRes {
    n: f64,
    hit: f64,
    bn: f64,
    bhit: f64,
    prec: f64,
    prec_lo: f64,
    prec_hi: f64,
    base: f64,
    base_lo: f64,
    base_hi: f64,
    lift: f64,
    lo: f64,
    hi: f64,
    p: f64,
    valid: usize,
}

fn lift_results(t: &Table, vp: &VParams, threads: usize) -> Result<Vec<LiftRes>> {
    let v = &vp.verdict;
    let reps = bootstrap::<4, 3, _>(&t.lift, t.days, v.bootstrap_b, v.seed, threads, |s, out| {
        out[0] = lift_of(s[0], s[1], s[2], s[3]);
        out[1] = ratio(s[1], s[0]);
        out[2] = ratio(s[3], s[2]);
    })?;
    Ok((0..t.keys.len())
        .map(|c| {
            let [n, hit, bn, bhit] = t.lift.totals(c);
            let l = interval(reps.get(c, 0), v.ci_level, 1.0);
            let pr = interval(reps.get(c, 1), v.ci_level, 0.0);
            let ba = interval(reps.get(c, 2), v.ci_level, 0.0);
            LiftRes {
                n,
                hit,
                bn,
                bhit,
                prec: ratio(hit, n),
                prec_lo: pr.lo,
                prec_hi: pr.hi,
                base: ratio(bhit, bn),
                base_lo: ba.lo,
                base_hi: ba.hi,
                lift: lift_of(n, hit, bn, bhit),
                lo: l.lo,
                hi: l.hi,
                p: l.p,
                valid: l.valid,
            }
        })
        .collect())
}

const LIFT_COLS: &str = "fam,wi,ti,theta,w_min,sign,hi,h_min,n,hit,precision,prec_lo,prec_hi,bn,bhit,base,base_lo,base_hi,lift,lift_lo,lift_hi,b_valid,p";

fn lift_line(vp: &VParams, k: Key, r: &LiftRes) -> String {
    format!(
        "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
        k.fam,
        k.wi,
        k.ti,
        vp.theta(k.ti),
        vp.window(k.wi),
        k.sign,
        k.hi,
        vp.h_min(k.hi),
        r.n,
        r.hit,
        f6(r.prec),
        f6(r.prec_lo),
        f6(r.prec_hi),
        r.bn,
        r.bhit,
        f6(r.base),
        f6(r.base_lo),
        f6(r.base_hi),
        f6(r.lift),
        f6(r.lo),
        f6(r.hi),
        r.valid,
        f6(r.p)
    )
}

// ---------- net ----------

#[derive(Debug, Clone, Copy)]
struct NetRes {
    nret: f64,
    gross_mean: f64,
    fund_mean: f64,
    main: f64,
    main_lo: f64,
    main_hi: f64,
    second: f64,
    second_lo: f64,
    second_hi: f64,
    p: f64,
}

fn net_results(t: &Table, vp: &VParams, fees: (f64, f64), threads: usize) -> Result<Vec<NetRes>> {
    let v = &vp.verdict;
    let (fee_main, fee_second) = fees;
    let reps = bootstrap::<3, 2, _>(&t.net, t.days, v.bootstrap_b, v.seed, threads, |s, out| {
        if s[0] > 0.0 {
            let m = (s[1] - s[2]) / s[0];
            out[0] = m - fee_main;
            out[1] = m - fee_second;
        }
    })?;
    Ok((0..t.keys.len())
        .map(|c| {
            let [nret, gross, fund] = t.net.totals(c);
            let gm = ratio(gross, nret);
            let fm = ratio(fund, nret);
            let a = interval(reps.get(c, 0), v.ci_level, 0.0);
            let b = interval(reps.get(c, 1), v.ci_level, 0.0);
            NetRes {
                nret,
                gross_mean: gm,
                fund_mean: fm,
                main: gm - fm - fee_main,
                main_lo: a.lo,
                main_hi: a.hi,
                second: gm - fm - fee_second,
                second_lo: b.lo,
                second_hi: b.hi,
                p: a.p,
            }
        })
        .collect())
}

#[derive(Debug, Clone, Copy, Default)]
struct MonthCnt {
    lift_gt1: usize,
    lift_def: usize,
    net_pos: usize,
    net_def: usize,
}

fn month_counts(t: &Table, c: usize, fee_main: f64) -> MonthCnt {
    let mut r = MonthCnt::default();
    for m in &t.monthly[c] {
        let l = lift_of(m[0], m[1], m[2], m[3]);
        if !l.is_nan() {
            r.lift_def += 1;
            if l > 1.0 {
                r.lift_gt1 += 1;
            }
        }
        if m[4] > 0.0 {
            r.net_def += 1;
            if (m[5] - m[6]) / m[4] - fee_main > 0.0 {
                r.net_pos += 1;
            }
        }
    }
    r
}

// ---------- вердикт по lift ----------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Verdict {
    Confirmed,
    Contested,
    Indistinct,
}

impl Verdict {
    fn label(self) -> &'static str {
        match self {
            Verdict::Confirmed => "подтверждено",
            Verdict::Contested => "оспорено",
            Verdict::Indistinct => "не различимо",
        }
    }
}

/// Входы правила вердикта §5.
pub(super) struct LiftIn {
    pub(super) fit_lo: f64,
    pub(super) test_lo: f64,
    pub(super) test_hi: f64,
    pub(super) p_holm: f64,
    pub(super) months_gt1: usize,
    pub(super) months_total: usize,
}

/// Доля месяцев `gt/total >= num/den` (целочисленно); пустой знаменатель — не выполнено.
pub(super) fn share_ok(gt: usize, total: usize, share: &VShare) -> bool {
    total > 0 && gt * share.den >= share.num * total
}

/// Правило вердикта §5: подтверждено = нижняя граница lift > 1 в фите И в тесте, p-Холм < α, lift > 1 не менее чем
/// в `min_months_share` месяцев; оспорено = верхняя граница в тесте < 1; иначе не различимо.
pub(super) fn lift_verdict(vp: &VParams, i: &LiftIn) -> Verdict {
    let v = &vp.verdict;
    if i.fit_lo > 1.0
        && i.test_lo > 1.0
        && i.p_holm < v.holm_alpha
        && share_ok(i.months_gt1, i.months_total, &v.min_months_share)
    {
        Verdict::Confirmed
    } else if i.test_hi < 1.0 {
        Verdict::Contested
    } else {
        Verdict::Indistinct
    }
}

// ---------- стадия fit ----------

/// Кандидат отбора: правило с рангом.
#[derive(Debug, Clone, Copy)]
struct Cand {
    rule: Rule,
    theta: f64,
    w: u32,
    rank: f64,
    lo: [f64; 2],
    lift: [f64; 2],
}

/// Порядок предпочтения (допущение 2): больший ранг, затем меньший θ, меньший w, затем индексы.
fn cmp_pref(a: &Cand, b: &Cand) -> Ordering {
    b.rank
        .total_cmp(&a.rank)
        .then(a.theta.total_cmp(&b.theta))
        .then(a.w.cmp(&b.w))
        .then(a.rule.cmp(&b.rule))
}

fn fit_stage(a: &VerdictArgs, vp: &VParams, sha: &str) -> Result<()> {
    let (rows, _) = guard_months(
        read_daycells(&a.input.join("daycells-fit.csv"))?,
        Stage::Fit,
        vp,
    )?;
    let t = build_table(&rows, None)?;
    for k in &t.keys {
        vp.check_rule(k.rule())?;
        if usize::from(k.hi) >= vp.label_forward.h_min.len() {
            bail!("hi = {} вне label_forward.H_min", k.hi);
        }
    }
    let lr = lift_results(&t, vp, a.threads)?;
    let ih = vp.hi_of(vp.verdict.rank.h_min)?;

    let mut lines = vec![LIFT_COLS.to_string()];
    for (k, r) in t.keys.iter().zip(&lr) {
        lines.push(lift_line(vp, *k, r));
    }
    write_lines(&a.out.join("lift-fit.csv"), &lines)?;

    // правила: ячейки при H ранга, по знакам (−1, +1)
    let mut rules: BTreeMap<Rule, [Option<usize>; 2]> = BTreeMap::new();
    for (c, k) in t.keys.iter().enumerate() {
        if usize::from(k.hi) == ih {
            rules.entry(k.rule()).or_default()[usize::from(k.sign > 0)] = Some(c);
        }
    }
    let mut cands: Vec<Cand> = Vec::new();
    let mut ranked_rows: Vec<(Rule, [Option<usize>; 2], Option<f64>)> = Vec::new();
    for (rule, cells) in &rules {
        let rank = match cells {
            [Some(x), Some(y)]
                if lr[*x].lift.is_finite()
                    && lr[*y].lift.is_finite()
                    && lr[*x].lo.is_finite()
                    && lr[*y].lo.is_finite() =>
            {
                Some((lr[*x].lo + lr[*y].lo) / 2.0)
            }
            _ => None,
        };
        if let (Some(rank), [Some(x), Some(y)]) = (rank, cells) {
            cands.push(Cand {
                rule: *rule,
                theta: vp.theta(rule.ti),
                w: vp.window(rule.wi),
                rank,
                lo: [lr[*x].lo, lr[*y].lo],
                lift: [lr[*x].lift, lr[*y].lift],
            });
        }
        ranked_rows.push((*rule, *cells, rank));
    }
    // лучшее правило каждого семейства
    let mut best: Vec<Cand> = Vec::new();
    for fam in 0..vp.verdict.families {
        if let Some(c) = cands
            .iter()
            .filter(|c| usize::from(c.rule.fam) == fam)
            .min_by(|a, b| cmp_pref(a, b))
        {
            best.push(*c);
        }
    }
    best.sort_by(cmp_pref);
    let sel: Vec<Cand> = best.iter().copied().take(vp.verdict.k).collect();

    let mut rl = vec![
        "fam,wi,ti,theta,w_min,lo_minus,lo_plus,lift_minus,lift_plus,rank_lo,status,family_best,k_pos"
            .to_string(),
    ];
    for (rule, cells, rank) in &ranked_rows {
        let at = |s: usize, f: fn(&LiftRes) -> f64| cells[s].map_or(f64::NAN, |c| f(&lr[c]));
        let is_best = best.iter().any(|c| c.rule == *rule);
        let kpos = sel
            .iter()
            .position(|c| c.rule == *rule)
            .map_or(0, |p| p + 1);
        rl.push(format!(
            "{},{},{},{},{},{},{},{},{},{},{},{},{}",
            rule.fam,
            rule.wi,
            rule.ti,
            vp.theta(rule.ti),
            vp.window(rule.wi),
            f6(at(0, |r| r.lo)),
            f6(at(1, |r| r.lo)),
            f6(at(0, |r| r.lift)),
            f6(at(1, |r| r.lift)),
            f6(rank.unwrap_or(f64::NAN)),
            if rank.is_some() { "ranked" } else { "no_data" },
            u8::from(is_best),
            kpos
        ));
    }
    write_lines(&a.out.join("rank-fit.csv"), &rl)?;

    let kj: Vec<serde_json::Value> = sel
        .iter()
        .enumerate()
        .map(|(i, c)| {
            serde_json::json!({
                "pos": i + 1,
                "fam": c.rule.fam,
                "wi": c.rule.wi,
                "ti": c.rule.ti,
                "family": fam_name(c.rule.fam),
                "theta": c.theta,
                "w_min": c.w,
                "rank_lo": c.rank,
                "lo_minus": c.lo[0],
                "lo_plus": c.lo[1],
                "lift_minus": c.lift[0],
                "lift_plus": c.lift[1],
            })
        })
        .collect();
    let mut kt = serde_json::to_string_pretty(&serde_json::Value::Array(kj))?;
    kt.push('\n');
    fs::write(a.out.join("k.json"), kt)?;

    let nodata = ranked_rows.iter().filter(|r| r.2.is_none()).count();
    let list: Vec<String> = sel
        .iter()
        .map(|c| format!("{} rank_lo={:.3}", rule_label(vp, c.rule), c.rank))
        .collect();
    let line = format!(
        "П-34 · фит {}…{} · отбор K={} из {} семейств до открытия теста (params sha256 {}): {}. Ранг = нижняя граница {:.0} % \
         lift точности при H={}, среднее по знакам; бутстрэп по суткам B={}, seed={}. Правил в фите {}, без ранга (нет данных/база 0) {}; \
         ячеек (правило×знак×H) {} при протоколе {}. Файлы: k.json, rank-fit.csv, lift-fit.csv.",
        vp.split.fit[0],
        vp.split.fit[1],
        sel.len(),
        vp.verdict.families,
        &sha[..sha.len().min(12)],
        list.join("; "),
        vp.verdict.ci_level * 100.0,
        vp.verdict.rank.h_min,
        vp.verdict.bootstrap_b,
        vp.verdict.seed,
        ranked_rows.len(),
        nodata,
        t.keys.len(),
        vp.trials.fit_total
    );
    fs::write(a.out.join("journal-line.txt"), format!("{line}\n"))?;
    println!("{line}");
    Ok(())
}

// ---------- стадия test ----------

fn read_k(path: &Path, vp: &VParams) -> Result<Vec<Rule>> {
    let text = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    let v: serde_json::Value = serde_json::from_str(&text).context("k.json")?;
    let arr = v.as_array().context("k.json: ожидался массив")?;
    let mut out: Vec<Rule> = Vec::new();
    for (i, o) in arr.iter().enumerate() {
        let g = |name: &str| -> Result<u8> {
            o.get(name)
                .and_then(serde_json::Value::as_u64)
                .and_then(|x| u8::try_from(x).ok())
                .with_context(|| format!("k.json[{i}].{name}"))
        };
        let rule = Rule {
            fam: g("fam")?,
            wi: g("wi")?,
            ti: g("ti")?,
        };
        vp.check_rule(rule)?;
        if out.contains(&rule) {
            bail!("k.json[{i}]: повтор правила");
        }
        out.push(rule);
    }
    if out.is_empty() || out.len() > vp.verdict.k {
        bail!(
            "k.json: правил {}, ожидалось 1..={}",
            out.len(),
            vp.verdict.k
        );
    }
    Ok(out)
}

/// Итог по (правило, знак) при основном H: условия «встать рано можно» (§7.6, допущение 6).
#[derive(Debug, Clone, Copy)]
struct Early {
    rule: Rule,
    sign: i8,
    lift_ok: bool,
    net_lo_ok: bool,
    net_p_ok: bool,
    net_months_ok: bool,
}

impl Early {
    fn passed(&self) -> usize {
        [
            self.lift_ok,
            self.net_lo_ok,
            self.net_p_ok,
            self.net_months_ok,
        ]
        .iter()
        .filter(|x| **x)
        .count()
    }

    fn ok(&self) -> bool {
        self.passed() == 4
    }

    /// Что не сошлось (тексты без запятых — идут в csv).
    fn fails(&self) -> Vec<&'static str> {
        let mut v = Vec::new();
        if !self.lift_ok {
            v.push("lift не подтверждён (§5)");
        }
        if !self.net_lo_ok {
            v.push("нижняя граница net <= 0");
        }
        if !self.net_p_ok {
            v.push("p-Холм(net) >= alpha");
        }
        if !self.net_months_ok {
            v.push("net > 0 менее чем в 2/3 месяцев");
        }
        v
    }
}

/// Сводка lags: суммы счётчиков по месяцам.
#[derive(Debug, Clone, Copy, Default)]
struct LagAgg {
    n_ep: u64,
    n_fired: u64,
    n_lt0: u64,
    n_le0: u64,
    n_le30: u64,
    n_le60: u64,
}

/// (вид, страта, правило, знак эпизода, класс D).
type LagKey = (u8, u8, Rule, i8, u8);

fn read_lags(path: &Path, k: &[Rule]) -> Result<BTreeMap<LagKey, LagAgg>> {
    let mut m: BTreeMap<LagKey, LagAgg> = BTreeMap::new();
    for (i, rec) in read_csv(path, &LAGS_HEAD)?.iter().enumerate() {
        let mut one = || -> Result<()> {
            let rule = Rule {
                fam: field(rec, 2, "fam")?,
                wi: field(rec, 3, "wi")?,
                ti: field(rec, 4, "ti")?,
            };
            if !k.contains(&rule) {
                return Ok(());
            }
            let key = (
                idx_of(&KINDS, rec.get(0).unwrap_or(""), "kind")?,
                idx_of(&STRATA, rec.get(1).unwrap_or(""), "stratum")?,
                rule,
                sign_of(rec, 5)?,
                field::<u8>(rec, 6, "di")?,
            );
            let r = LagAgg {
                n_ep: field(rec, 8, "n_ep")?,
                n_fired: field(rec, 9, "n_fired")?,
                n_lt0: field(rec, 10, "n_lt0")?,
                n_le0: field(rec, 11, "n_le0")?,
                n_le30: field(rec, 12, "n_le30")?,
                n_le60: field(rec, 13, "n_le60")?,
            };
            if !(r.n_lt0 <= r.n_le0
                && r.n_le0 <= r.n_le30
                && r.n_le30 <= r.n_le60
                && r.n_le60 <= r.n_fired
                && r.n_fired <= r.n_ep)
            {
                bail!("счётчики лагов не монотонны (n_lt0<=n_le0<=n_le30<=n_le60<=n_fired<=n_ep)");
            }
            let a = m.entry(key).or_default();
            a.n_ep += r.n_ep;
            a.n_fired += r.n_fired;
            a.n_lt0 += r.n_lt0;
            a.n_le0 += r.n_le0;
            a.n_le30 += r.n_le30;
            a.n_le60 += r.n_le60;
            Ok(())
        };
        one().with_context(|| format!("{} строка {}", path.display(), i + 2))?;
    }
    Ok(m)
}

/// (страта, wi, ti, знак, класс D, семейство) → [(n_ep, n_first) эпизоды, (то же) C2b].
type FirstKey = (u8, u8, u8, i8, u8, u8);

fn read_first(path: &Path) -> Result<BTreeMap<FirstKey, [(u64, u64); 2]>> {
    let mut m: BTreeMap<FirstKey, [(u64, u64); 2]> = BTreeMap::new();
    for (i, rec) in read_csv(path, &FIRST_HEAD)?.iter().enumerate() {
        let mut one = || -> Result<()> {
            let kind = idx_of(&KINDS, rec.get(0).unwrap_or(""), "kind")?;
            let key = (
                idx_of(&STRATA, rec.get(1).unwrap_or(""), "stratum")?,
                field::<u8>(rec, 2, "wi")?,
                field::<u8>(rec, 3, "ti")?,
                sign_of(rec, 4)?,
                field::<u8>(rec, 5, "di")?,
                field::<u8>(rec, 6, "fam")?,
            );
            let (n_ep, n_first): (u64, u64) = (field(rec, 7, "n_ep")?, field(rec, 8, "n_first")?);
            if n_first > n_ep {
                bail!("n_first > n_ep");
            }
            m.entry(key).or_default()[usize::from(kind)] = (n_ep, n_first);
            Ok(())
        };
        one().with_context(|| format!("{} строка {}", path.display(), i + 2))?;
    }
    Ok(m)
}

/// Взвешенные по `n_fired` помесячные медианы хода (допущение 8).
#[derive(Debug, Clone, Copy, Default)]
struct ProgAgg {
    n: f64,
    months: usize,
    done: f64,
    left: f64,
    peak: f64,
}

fn read_progress(path: &Path, k: &[Rule]) -> Result<BTreeMap<(Rule, i8, u8), ProgAgg>> {
    let mut m: BTreeMap<(Rule, i8, u8), ProgAgg> = BTreeMap::new();
    for (i, rec) in read_csv(path, &PROGRESS_HEAD)?.iter().enumerate() {
        let mut one = || -> Result<()> {
            if rec.get(0).unwrap_or("") != "all" {
                return Ok(());
            }
            let rule = Rule {
                fam: field(rec, 1, "fam")?,
                wi: field(rec, 2, "wi")?,
                ti: field(rec, 3, "ti")?,
            };
            if !k.contains(&rule) {
                return Ok(());
            }
            let n: u64 = field(rec, 7, "n_fired")?;
            let (d, l, p) = (
                opt_f64(rec, 9, "done_bps_p50")?,
                opt_f64(rec, 12, "left_share_p50")?,
                opt_f64(rec, 15, "to_peak_bps_p50")?,
            );
            if let (true, Some(d), Some(l), Some(p)) = (n > 0, d, l, p) {
                let a = m
                    .entry((rule, sign_of(rec, 4)?, field::<u8>(rec, 5, "di")?))
                    .or_default();
                a.n += n as f64;
                a.months += 1;
                a.done += n as f64 * d;
                a.left += n as f64 * l;
                a.peak += n as f64 * p;
            }
            Ok(())
        };
        one().with_context(|| format!("{} строка {}", path.display(), i + 2))?;
    }
    Ok(m)
}

fn share(a: u64, b: u64) -> f64 {
    ratio(a as f64, b as f64)
}

struct TestOut {
    cells: Vec<TestCell>,
    early: Vec<Early>,
}

#[derive(Debug, Clone, Copy)]
struct TestCell {
    key: Key,
    lift: LiftRes,
    fit: LiftRes,
    p_holm: f64,
    months: MonthCnt,
    months_total: usize,
    verdict: Verdict,
    net: NetRes,
    /// `NaN` у описательных H.
    net_p_holm: f64,
}

fn test_stage(a: &VerdictArgs, vp: &VParams, sha: &str) -> Result<()> {
    let v = &vp.verdict;
    let kpath = a.k.as_ref().context("--k обязателен для стадии test")?;
    let krules = read_k(kpath, vp)?;
    let nh = vp.label_forward.h_min.len();
    let ih_net = vp.hi_of(v.net.h_min)?;
    let mut want: Vec<Key> = Vec::new();
    for r in &krules {
        for sign in [-1i8, 1] {
            for hi in 0..nh {
                want.push(Key {
                    fam: r.fam,
                    wi: r.wi,
                    ti: r.ti,
                    sign,
                    hi: hi as u8,
                });
            }
        }
    }
    want.sort_unstable();

    let (rows_t, dropped) = guard_months(
        read_daycells(&a.input.join(format!("daycells-{}.csv", Stage::Test.name())))?,
        Stage::Test,
        vp,
    )?;
    for r in &rows_t {
        if usize::from(r.key.hi) >= nh {
            bail!("hi = {} вне label_forward.H_min", r.key.hi);
        }
    }
    let fit_dir = a.in_fit.as_ref().unwrap_or(&a.input);
    let (rows_f, _) = guard_months(
        read_daycells(&fit_dir.join("daycells-fit.csv"))?,
        Stage::Fit,
        vp,
    )?;
    // знаменатель доли месяцев — все месяцы периода теста, а не присутствующие в файле (допущение 4)
    let test_months = period_months(&vp.split.test)?;
    let tt = build_table(&rows_t, Some(want.as_slice()))?;
    let tf = build_table(&rows_f, Some(want.as_slice()))?;
    let not_k = rows_t
        .iter()
        .filter(|r| want.binary_search(&r.key).is_err())
        .count();

    let fees = (2.0 * leg_fee_bps(true), ROUNDTRIP_FEES_BPS);
    let lt = lift_results(&tt, vp, a.threads)?;
    let lf = lift_results(&tf, vp, a.threads)?;
    let nt = net_results(&tt, vp, fees, a.threads)?;

    let ps: Vec<f64> = lt.iter().map(|r| r.p).collect();
    let ph = holm(&ps, vp.trials.test_lift_m);
    let main_idx: Vec<usize> = (0..tt.keys.len())
        .filter(|&c| usize::from(tt.keys[c].hi) == ih_net)
        .collect();
    let pn: Vec<f64> = main_idx.iter().map(|&c| nt[c].p).collect();
    let phn = holm(&pn, vp.trials.test_net_m);
    let mut net_ph = vec![f64::NAN; tt.keys.len()];
    for (j, &c) in main_idx.iter().enumerate() {
        net_ph[c] = phn[j];
    }

    let mut cells = Vec::with_capacity(tt.keys.len());
    for (c, key) in tt.keys.iter().enumerate() {
        let months = month_counts(&tt, c, fees.0);
        let months_total = test_months.len();
        let verdict = lift_verdict(
            vp,
            &LiftIn {
                fit_lo: lf[c].lo,
                test_lo: lt[c].lo,
                test_hi: lt[c].hi,
                p_holm: ph[c],
                months_gt1: months.lift_gt1,
                months_total,
            },
        );
        cells.push(TestCell {
            key: *key,
            lift: lt[c],
            fit: lf[c],
            p_holm: ph[c],
            months,
            months_total,
            verdict,
            net: nt[c],
            net_p_holm: net_ph[c],
        });
    }

    // «встать рано»: (правило, знак) при основном H
    let mut early = Vec::new();
    for r in &krules {
        for sign in [-1i8, 1] {
            let key = Key {
                fam: r.fam,
                wi: r.wi,
                ti: r.ti,
                sign,
                hi: ih_net as u8,
            };
            let c = pos(&tt.keys, &key, "ячейка")?;
            let cell = &cells[c];
            early.push(Early {
                rule: *r,
                sign,
                lift_ok: cell.verdict == Verdict::Confirmed,
                net_lo_ok: cell.net.main_lo > 0.0,
                net_p_ok: cell.net_p_holm < v.holm_alpha,
                net_months_ok: share_ok(
                    cell.months.net_pos,
                    cell.months_total,
                    &v.min_months_share,
                ),
            });
        }
    }
    let out = TestOut { cells, early };

    write_test_csvs(a, vp, &out)?;

    let lags = read_lags(&a.input.join("lags-test.csv"), &krules)?;
    write_recall(a, vp, &lags)?;
    let first = read_first(&a.input.join("first-test.csv"))?;
    write_first_diff(a, vp, &first)?;
    let prog_path = a.input.join("progress-test.csv");
    let prog = if prog_path.exists() {
        Some(read_progress(&prog_path, &krules)?)
    } else {
        None
    };
    if let Some(p) = &prog {
        write_progress(a, vp, p)?;
    }

    let report = build_report(&ReportIn {
        vp,
        sha,
        tt: &tt,
        test_months: &test_months,
        krules: &krules,
        out: &out,
        lags: &lags,
        first: &first,
        prog: prog.as_ref(),
        dropped,
        not_k,
    });
    fs::write(a.out.join("report-tables.md"), report)?;
    for line in early_summary(vp, &out.early) {
        println!("{line}");
    }
    Ok(())
}

fn write_test_csvs(a: &VerdictArgs, vp: &VParams, o: &TestOut) -> Result<()> {
    let mut vl = vec![format!(
        "{LIFT_COLS},p_holm,fit_lift,fit_lift_lo,fit_lift_hi,months_gt1,months_defined,months_total,verdict"
    )];
    let mut nl = vec![
        "fam,wi,ti,theta,w_min,sign,hi,h_min,main,nret,gross_mean,fund_mean,fee_main,fee_second,net_main,net_main_lo,net_main_hi,p,p_holm,net_second,net_second_lo,net_second_hi,breakeven_slip_main,breakeven_slip_second,months_pos,months_defined,months_total"
            .to_string(),
    ];
    let ih_net = vp.hi_of(vp.verdict.net.h_min)?;
    let (fee_main, fee_second) = (2.0 * leg_fee_bps(true), ROUNDTRIP_FEES_BPS);
    for c in &o.cells {
        vl.push(format!(
            "{},{},{},{},{},{},{},{},{}",
            lift_line(vp, c.key, &c.lift),
            f6(c.p_holm),
            f6(c.fit.lift),
            f6(c.fit.lo),
            f6(c.fit.hi),
            c.months.lift_gt1,
            c.months.lift_def,
            c.months_total,
            c.verdict.label()
        ));
        let n = &c.net;
        nl.push(format!(
            "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            c.key.fam,
            c.key.wi,
            c.key.ti,
            vp.theta(c.key.ti),
            vp.window(c.key.wi),
            c.key.sign,
            c.key.hi,
            vp.h_min(c.key.hi),
            u8::from(usize::from(c.key.hi) == ih_net),
            n.nret,
            f6(n.gross_mean),
            f6(n.fund_mean),
            f6(fee_main),
            f6(fee_second),
            f6(n.main),
            f6(n.main_lo),
            f6(n.main_hi),
            f6(n.p),
            f6(c.net_p_holm),
            f6(n.second),
            f6(n.second_lo),
            f6(n.second_hi),
            f6(n.main),
            f6(n.second),
            c.months.net_pos,
            c.months.net_def,
            c.months_total
        ));
    }
    write_lines(&a.out.join("verdict-test.csv"), &vl)?;
    write_lines(&a.out.join("net-test.csv"), &nl)?;
    let mut el = vec![
        "fam,wi,ti,theta,w_min,sign,lift_confirmed,net_lo_gt0,net_p_holm_ok,net_months_ok,early,fail"
            .to_string(),
    ];
    for e in &o.early {
        el.push(format!(
            "{},{},{},{},{},{},{},{},{},{},{},{}",
            e.rule.fam,
            e.rule.wi,
            e.rule.ti,
            vp.theta(e.rule.ti),
            vp.window(e.rule.wi),
            e.sign,
            u8::from(e.lift_ok),
            u8::from(e.net_lo_ok),
            u8::from(e.net_p_ok),
            u8::from(e.net_months_ok),
            if e.ok() {
                "можно"
            } else {
                "не доказано"
            },
            e.fails().join("; ")
        ));
    }
    write_lines(&a.out.join("early-test.csv"), &el)
}

fn write_recall(a: &VerdictArgs, vp: &VParams, lags: &BTreeMap<LagKey, LagAgg>) -> Result<()> {
    let mut l = vec![
        "kind,stratum,fam,wi,ti,theta,w_min,sign,di,n_ep,n_fired,n_lt0,n_le0,n_le30,n_le60,recall0,recall30,recall60,share_lt0_of_ep,share_lt0_of_fired"
            .to_string(),
    ];
    for ((kind, st, rule, sign, di), g) in lags {
        l.push(format!(
            "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            KINDS[usize::from(*kind)],
            STRATA[usize::from(*st)],
            rule.fam,
            rule.wi,
            rule.ti,
            vp.theta(rule.ti),
            vp.window(rule.wi),
            sign,
            di,
            g.n_ep,
            g.n_fired,
            g.n_lt0,
            g.n_le0,
            g.n_le30,
            g.n_le60,
            f6(share(g.n_le0, g.n_ep)),
            f6(share(g.n_le30, g.n_ep)),
            f6(share(g.n_le60, g.n_ep)),
            f6(share(g.n_lt0, g.n_ep)),
            f6(share(g.n_lt0, g.n_fired))
        ));
    }
    write_lines(&a.out.join("recall-test.csv"), &l)
}

fn write_first_diff(
    a: &VerdictArgs,
    vp: &VParams,
    first: &BTreeMap<FirstKey, [(u64, u64); 2]>,
) -> Result<()> {
    let mut l = vec![
        "stratum,wi,ti,theta,w_min,sign,di,fam,n_ep_ep,n_first_ep,share_ep,n_ep_c2b,n_first_c2b,share_c2b,diff"
            .to_string(),
    ];
    for ((st, wi, ti, sign, di, fam), kinds) in first {
        let (e, c) = (kinds[0], kinds[1]);
        let (se, sc) = (share(e.1, e.0), share(c.1, c.0));
        l.push(format!(
            "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            STRATA[usize::from(*st)],
            wi,
            ti,
            vp.theta(*ti),
            vp.window(*wi),
            sign,
            di,
            fam,
            e.0,
            e.1,
            f6(se),
            c.0,
            c.1,
            f6(sc),
            f6(se - sc)
        ));
    }
    write_lines(&a.out.join("first-diff-test.csv"), &l)
}

fn write_progress(
    a: &VerdictArgs,
    vp: &VParams,
    prog: &BTreeMap<(Rule, i8, u8), ProgAgg>,
) -> Result<()> {
    let mut l = vec![
        "fam,wi,ti,theta,w_min,sign,di,n_fired,months,done_bps_p50_wmean,left_share_p50_wmean,to_peak_bps_p50_wmean"
            .to_string(),
    ];
    for ((rule, sign, di), g) in prog {
        l.push(format!(
            "{},{},{},{},{},{},{},{},{},{},{},{}",
            rule.fam,
            rule.wi,
            rule.ti,
            vp.theta(rule.ti),
            vp.window(rule.wi),
            sign,
            di,
            g.n,
            g.months,
            f6(ratio(g.done, g.n)),
            f6(ratio(g.left, g.n)),
            f6(ratio(g.peak, g.n))
        ));
    }
    write_lines(&a.out.join("progress-summary-test.csv"), &l)
}

// ---------- итог §7.6 и отчёт ----------

/// Строки итога по знакам: «встать рано можно» / «не доказано» с названием, что не сошлось.
fn early_summary(vp: &VParams, early: &[Early]) -> Vec<String> {
    let mut out = Vec::new();
    for (sign, name) in [(1i8, "Пампы (sign +1)"), (-1i8, "Дампы (sign −1)")] {
        let rows: Vec<&Early> = early.iter().filter(|e| e.sign == sign).collect();
        let ok: Vec<String> = rows
            .iter()
            .filter(|e| e.ok())
            .map(|e| rule_label(vp, e.rule))
            .collect();
        if !ok.is_empty() {
            out.push(format!(
                "{name}: встать рано можно — правил, прошедших все условия §7.6: {} ({}).",
                ok.len(),
                ok.join("; ")
            ));
            continue;
        }
        let nearest = rows
            .iter()
            .max_by(|a, b| a.passed().cmp(&b.passed()).then(b.rule.cmp(&a.rule)));
        let mut s = format!("{name}: не доказано");
        if let Some(n) = nearest {
            s.push_str(&format!(
                ". Ближайшее правило {} прошло {} из 4 условий, не сошлось: {}",
                rule_label(vp, n.rule),
                n.passed(),
                n.fails().join("; ")
            ));
        } else {
            s.push_str(": нет правил");
        }
        let cnt = |f: fn(&Early) -> bool| rows.iter().filter(|e| !f(e)).count();
        s.push_str(&format!(
            ". Не сошлось по правилам (из {}): lift {} · нижняя граница net {} · p-Холм net {} · месяцы net {}.",
            rows.len(),
            cnt(|e| e.lift_ok),
            cnt(|e| e.net_lo_ok),
            cnt(|e| e.net_p_ok),
            cnt(|e| e.net_months_ok)
        ));
        out.push(s);
    }
    out
}

struct ReportIn<'a> {
    vp: &'a VParams,
    sha: &'a str,
    tt: &'a Table,
    /// Все месяцы периода `split.test` (знаменатель долей месяцев).
    test_months: &'a [i32],
    krules: &'a [Rule],
    out: &'a TestOut,
    lags: &'a BTreeMap<LagKey, LagAgg>,
    first: &'a BTreeMap<FirstKey, [(u64, u64); 2]>,
    prog: Option<&'a BTreeMap<(Rule, i8, u8), ProgAgg>>,
    dropped: usize,
    not_k: usize,
}

fn build_report(r: &ReportIn) -> String {
    let vp = r.vp;
    let v = &vp.verdict;
    let mut d = Doc(String::new());
    d.line("# П-34 · вердикт теста — таблицы (verdict --stage test)");
    d.line("");
    d.line(&format!(
        "Параметры: sha256 `{}`; бутстрэп по суткам (все монеты вместе) B={}, seed={}, интервал {:.0} % = перцентили {:.1}/{:.1}; \
         Холм: lift m={} (ячеек {}), net m={} (основных ячеек {}). Суток-кластеров {}, месяцев теста {} ({}), из них с данными в `daycells-test` {}.",
        r.sha,
        v.bootstrap_b,
        v.seed,
        v.ci_level * 100.0,
        (1.0 - v.ci_level) * 50.0,
        100.0 - (1.0 - v.ci_level) * 50.0,
        vp.trials.test_lift_m,
        r.out.cells.len(),
        vp.trials.test_net_m,
        r.out.early.len(),
        r.tt.days,
        r.test_months.len(),
        r.test_months
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(", "),
        r.tt.months.len()
    ));
    d.line(&format!(
        "Правила K ({}): {}. Строк `daycells-test` вне замороженных правил: {} (не использованы); вне периода теста отброшено: {}.",
        r.krules.len(),
        r.krules
            .iter()
            .map(|x| rule_label(vp, *x))
            .collect::<Vec<_>>()
            .join("; "),
        r.not_k,
        r.dropped
    ));
    d.line("Чувствительность «монета × сутки»: не считалась (в `daycells` не восстановима).");
    d.line("");
    d.line("## Итог (П-34 §7.6)");
    for l in early_summary(vp, &r.out.early) {
        d.line(&format!("- {l}"));
    }
    d.line("");

    d.line("## 1. Точность против базы C2a (lift)");
    d.line(&md_row(&[
        "правило".into(),
        "знак".into(),
        "H".into(),
        "n".into(),
        "точность [95 %]".into(),
        "база C2a [95 %]".into(),
        "lift [95 %]".into(),
        "p-Холм".into(),
        "фит lift (нижн.)".into(),
        "мес. lift>1".into(),
        "вердикт".into(),
    ]));
    d.line(&md_sep(11));
    for c in &r.out.cells {
        let l = &c.lift;
        d.line(&md_row(&[
            rule_label(vp, c.key.rule()),
            format!("{:+}", c.key.sign),
            vp.h_min(c.key.hi).to_string(),
            format!("{}", l.n),
            format!("{} [{}; {}]", r3(l.prec), r3(l.prec_lo), r3(l.prec_hi)),
            format!("{} [{}; {}]", r3(l.base), r3(l.base_lo), r3(l.base_hi)),
            format!("{} [{}; {}]", r3(l.lift), r3(l.lo), r3(l.hi)),
            r3(c.p_holm),
            r3(c.fit.lo),
            format!("{}/{}", c.months.lift_gt1, c.months_total),
            c.verdict.label().to_string(),
        ]));
    }
    d.line("");

    d.line(&format!(
        "## 2. Net на сделку, bps (основной H={}; H {:?} — описательно, без Холма)",
        v.net.h_min, v.net.descriptive_h_min
    ));
    d.line("net = среднее(gross − фандинг) − издержки; основной вариант — тейкер/тейкер `2·leg_fee_bps(true)`, второй — `ROUNDTRIP_FEES_BPS` (мейкер-вход + тейкер-выход). Проскальзывание не вычитается; безубыточное проскальзывание (bps на круг) = net до его вычета.");
    d.line(&md_row(&[
        "правило".into(),
        "знак".into(),
        "H".into(),
        "сделок".into(),
        "gross".into(),
        "фандинг".into(),
        "net осн. [95 %]".into(),
        "p-Холм".into(),
        "net втор. [95 %]".into(),
        "безуб. проскальз.".into(),
        "мес. net>0".into(),
    ]));
    d.line(&md_sep(11));
    for c in &r.out.cells {
        let n = &c.net;
        d.line(&md_row(&[
            rule_label(vp, c.key.rule()),
            format!("{:+}", c.key.sign),
            vp.h_min(c.key.hi).to_string(),
            format!("{}", n.nret),
            r3(n.gross_mean),
            r3(n.fund_mean),
            format!("{} [{}; {}]", r3(n.main), r3(n.main_lo), r3(n.main_hi)),
            r3(c.net_p_holm),
            format!(
                "{} [{}; {}]",
                r3(n.second),
                r3(n.second_lo),
                r3(n.second_hi)
            ),
            r3(n.main),
            format!("{}/{}", c.months.net_pos, c.months_total),
        ]));
    }
    d.line("");

    d.line("## 3. Полнота и «заливка до»: эпизоды против нулевой линии C2b (страта all; суммы по месяцам теста)");
    d.line("recall(δ) = доля эпизодов с лагом ≤ δ мин; `лаг<0` — доля эпизодов, где правило сработало раньше старта. Квантили лага — помесячно в `lags-test.csv`.");
    d.line(&md_row(&[
        "правило".into(),
        "знак эп.".into(),
        "D".into(),
        "эпизодов".into(),
        "recall δ=0 (эп. / C2b)".into(),
        "δ=30".into(),
        "δ=60".into(),
        "лаг<0 (эп. / C2b)".into(),
    ]));
    d.line(&md_sep(8));
    for ((kind, st, rule, sign, di), g) in r.lags {
        if *kind != 0 || *st != 0 {
            continue;
        }
        let c2b = r
            .lags
            .get(&(1, 0, *rule, *sign, *di))
            .copied()
            .unwrap_or_default();
        let pair =
            |a: u64, b: u64| format!("{} / {}", r3(share(a, g.n_ep)), r3(share(b, c2b.n_ep)));
        d.line(&md_row(&[
            rule_label(vp, *rule),
            format!("{sign:+}"),
            di.to_string(),
            g.n_ep.to_string(),
            pair(g.n_le0, c2b.n_le0),
            pair(g.n_le30, c2b.n_le30),
            pair(g.n_le60, c2b.n_le60),
            pair(g.n_lt0, c2b.n_lt0),
        ]));
    }
    d.line("");

    d.line("## 4. Какой признак первым: разность долей эпизодов и C2b (страта all, три крупнейших по разности на (w, θ, знак); суммы по классам D)");
    d.line(&md_row(&[
        "w, θ".into(),
        "знак".into(),
        "признак".into(),
        "доля эпизодов".into(),
        "доля C2b".into(),
        "разность".into(),
    ]));
    d.line(&md_sep(6));
    let mut pairs: Vec<(u8, u8)> = r.krules.iter().map(|x| (x.wi, x.ti)).collect();
    pairs.sort_unstable();
    pairs.dedup();
    for (wi, ti) in pairs {
        for sign in [1i8, -1] {
            let mut per: BTreeMap<u8, [(u64, u64); 2]> = BTreeMap::new();
            for ((st, w2, t2, s2, _, fam), kinds) in r.first {
                if *st == 0 && *w2 == wi && *t2 == ti && *s2 == sign {
                    let e = per.entry(*fam).or_default();
                    for (ek, kk) in e.iter_mut().zip(kinds.iter()) {
                        ek.0 += kk.0;
                        ek.1 += kk.1;
                    }
                }
            }
            let mut rows: Vec<(u8, f64, f64)> = per
                .iter()
                .map(|(f, k)| (*f, share(k[0].1, k[0].0), share(k[1].1, k[1].0)))
                // доля без знаменателя (нет эпизодов или C2b) — `NaN`; `total_cmp` ставит её первой, поэтому до сортировки
                .filter(|(_, se, sc)| !(se - sc).is_nan())
                .collect();
            rows.sort_by(|a, b| (b.1 - b.2).total_cmp(&(a.1 - a.2)).then(a.0.cmp(&b.0)));
            for (f, se, sc) in rows.into_iter().take(3) {
                d.line(&md_row(&[
                    format!("w{} θ{}", vp.window(wi), vp.theta(ti)),
                    format!("{sign:+}"),
                    fam_name(f),
                    r3(se),
                    r3(sc),
                    r3(se - sc),
                ]));
            }
        }
    }
    d.line("");

    if let Some(p) = r.prog {
        d.line("## 5. Сколько хода прошло и осталось к моменту входа (страта all; взвешенное по срабатываниям среднее помесячных медиан — приближение)");
        d.line(&md_row(&[
            "правило".into(),
            "знак".into(),
            "D".into(),
            "срабатываний".into(),
            "пройдено, bps".into(),
            "осталось, доля".into(),
            "до пика, bps (верхняя граница)".into(),
        ]));
        d.line(&md_sep(7));
        for ((rule, sign, di), g) in p {
            d.line(&md_row(&[
                rule_label(vp, *rule),
                format!("{sign:+}"),
                di.to_string(),
                format!("{}", g.n),
                r3(ratio(g.done, g.n)),
                r3(ratio(g.left, g.n)),
                r3(ratio(g.peak, g.n)),
            ]));
        }
        d.line("");
    }

    d.line("## Оговорки");
    d.line(&format!(
        "- Число испытаний: lift — {} ячеек (правило × знак × H), Холм по m={}; net — {} основных ячеек, Холм по m={}; \
         расширений сетки после просмотра нет.",
        r.out.cells.len(),
        vp.trials.test_lift_m,
        r.out.early.len(),
        vp.trials.test_net_m
    ));
    d.line("- Реплика бутстрэпа с неопределённой статистикой не входит в границы интервала и считается «не отвергла нуль» в p.");
    d.line("- Месяцы теста: знаменатель долей — все месяцы периода `split.test`, а не только присутствующие в `daycells-test.csv`; месяц без данных ячейки не считается «lift > 1» / «net > 0».");
    d.line("- Метка «эпизод уже идёт» и страты outlier/wick в `daycells` не входят; вердикт — на всех срабатываниях правила.");
    d.0
}

// ---------- точка входа ----------

pub fn verdict(a: VerdictArgs) -> Result<()> {
    let text = fs::read_to_string(&a.params).with_context(|| a.params.display().to_string())?;
    let vp = VParams::parse(&text)?;
    let sha = hex(&Sha256::digest(text.as_bytes()));
    fs::create_dir_all(&a.out).with_context(|| a.out.display().to_string())?;
    match a.stage {
        Stage::Fit => fit_stage(&a, &vp, &sha),
        Stage::Test => test_stage(&a, &vp, &sha),
    }
}
