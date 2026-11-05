//! `alpha pumponset ignitions` — события воспламенения монеты (TK-492, слова владельца 05.11: «мы ищем не предпамп - а
//! первую фазу монеты инплей»; порог высокой волатильности — NATR за 30 одноминутных свечей ≥ 1).
//!
//! **Зачем.** Правила роста OI/объёма/сделок ловят воспламенение (в 4–17 раз лучше случайного), но условный анализ
//! направления «после воспламенения» построен на отборе по будущему: в момент сигнала неизвестно, воспламенится ли
//! монета. Торгуемая постановка — входить В МОМЕНТ воспламенения (когда NATR впервые пробивает порог после тишины),
//! направление выбирать по признакам момента. Подкоманда пишет события воспламенения отдельным набором данных.
//!
//! # Определения (ТЗ CEO 05.11)
//! 1. **Событие.** Минута `m` контракта, где `hv_state[m]` (`NATR_n(m) ≥ порога`, `play::natr_series`) истинно и ложно в
//!    КАЖДОЙ минуте `[m − L_quiet, m − 1]`, `L_quiet = onset.L_pre_min` (360). Это ровно `play::first_phase_mask`
//!    (`Play::first_at`): второе воспламенение внутри длинного `hv`-отрезка (и любое ближе `L_quiet` минут к прошлой
//!    минуте `hv_state`) — не событие, повторное после тишины — событие. Минуты вне сетки контракта и с неопределённым NATR —
//!    не `hv_state` (как в цели `play-onset`). Решение о событии принимается в минуте `m`: только данные `≤ m`.
//! 2. **Принадлежность.** Стадия — по `m` (период `split.fit` / `split.test`), вселенная — по суткам `m` (оборот суток
//!    `d − turnover_lag_days`, `run::Universe`); в файл идут события в вселенной, прочие считаются в
//!    `ignitions-meta-<stage>.json`. Контракты `is_leverage`, без свечей и без строк OI пропускаются (как в `run`).
//! 3. **Момент отсчёта** `t_f` — минута сетки 5 мин, не раньше `m`: `m`, округлённое ВВЕРХ до кратного слоту (минуты от
//!    `GRID0_MS`). Признаки считаются в слоте `t_f` (данные `≤ t_f`, включают минуту `m` — воспламенение уже наблюдается),
//!    вход — `c[t_f + 1]`. Событие, у которого цены входа нет (конец данных стадии), отбрасывается (`events_dropped_incomplete_path`);
//!    путь исхода короче 1440 минут не отбрасывает событие: исходы/`mfe`/`mae`, которым не хватило данных, — `NaN`, как в
//!    `triggers-<stage>.csv` (число таких событий — `events_kept_path_shorter_1440`).
//! 4. **Псевдо-правило.** Ключ строки — `fam = 11` (за пределами `feat::FAMILIES`), `wi = 0`, `ti = 0`, `sign = +1` ВСЕГДА:
//!    исходы `gross_*` — абсолютная доходность вверх (перспектива лонга), `mfe` — максимум, `mae` — минимум цены. Знак
//!    импульса — в `r_w_bps` (для окна `--sign-window`, по умолчанию окно 60 минут), `ret_prev60_bps`, `ret_prev360_bps`.
//!
//! # Вывод
//! `ignitions-<stage>.csv` (запятая, заголовок, контракт по алфавиту, затем `t_f`; байты не зависят от `--threads`) — те же
//! столбцы и определения, что `triggers-<stage>.csv` с `--dump-triggers --dump-features` (`dump.rs`, одна функция записи на
//! обе выгрузки: `Collector::write_core`), порядок: ключ … `mfe_price` (`dump::header_core`), `natr_pct` (NATR в `t_f`),
//! `dump::FEATURE_COLS`, затем `lead_m_since_quiet` (минут подряд без `hv_state` перед `m`, не больше `QUIET_CAP_MIN`,
//! считая только минуты сетки контракта), `natr_prev_max_6h` (максимум NATR, %, за `[m − 360, m − 1]`, `NaN` — ни одного
//! определённого), `m_minus_tf` (`t_f − m`, 0…4). Столбцов базы `b_gross_*` нет. `ep_running` — единственный столбец
//! атрибутов, не подчинённый правилу «только данные `≤ t_f`» (эпизоды `D` находятся задним числом; знак +1).
//!
//! `X_D` для эпизодов `ep_running` — как в `run` (из `--xd` либо два прохода по фиту). `--seed` на выход не влияет
//! (случайности нет), принят для совместимости строки запуска с `run`.

#![allow(
    // индексы ограничены длинами рядов контракта и куба, проверенными до обращения; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss
)]

use super::cache::SLOT_MIN;
use super::cli::{load_contract, sorted, sym_of};
use super::control::running_mask;
use super::dump::{self, Collector, IgnTail, Shared};
use super::episodes::Finder;
use super::feat;
use super::params::Params;
use super::play::{Play, PlayArgs, PlayCfg};
use super::run::{
    clip_contract, eligible, hex, par_map, peak_rss_mb, period, read_xd, xd_passes_for, Elig,
    Stage, Universe,
};
use super::types::{Rule, XdTable};
use anyhow::{ensure, Context, Result};
use clap::Args;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

/// Псевдо-правило события в столбцах ключа: семейство 11 (`feat::FAMILIES` — 0…10), окно и порог 0.
pub const IGN_RULE: Rule = Rule {
    fam: 11,
    wi: 0,
    ti: 0,
};
/// Окно признаков направления по умолчанию (`--sign-window`), мин — из ТЗ.
pub const SIGN_WINDOW_DEFAULT_MIN: u32 = 60;
/// Предел `lead_m_since_quiet` и окно `natr_prev_max_6h` назад от события, мин — из ТЗ.
pub const QUIET_CAP_MIN: i64 = 1440;
pub const PREV_MAX_MIN: i64 = 360;

#[derive(Debug, Args)]
pub struct IgnitionsArgs {
    /// Каталог `<SYM>.pmc` (`alpha pumponset cache`).
    #[arg(long)]
    pub cache: PathBuf,
    /// `P-34-params.json` (вселенная, периоды, `L_pre_min`, окна признаков).
    #[arg(long)]
    pub params: PathBuf,
    #[arg(long)]
    pub out: PathBuf,
    #[arg(long, value_enum)]
    pub stage: Stage,
    /// Порог высокой волатильности: `NATR_n ≥ порога`, % (порог владельца 1,0; значение задаёт запуск).
    #[arg(long)]
    pub hv_natr_pct: f64,
    /// Окно NATR, 1 м свечей (порог владельца 30; значение задаёт запуск).
    #[arg(long)]
    pub hv_n: usize,
    /// Сдвиг OI в слотах (Binance: 1), как у `run`.
    #[arg(long)]
    pub oi_lag: u32,
    #[arg(long)]
    pub threads: usize,
    /// Зерно: на выход не влияет (случайности нет); принят для совместимости строки запуска с `run`, пишется в meta.
    #[arg(long)]
    pub seed: Option<u64>,
    /// `xd.csv` фита (для эпизодов столбца `ep_running`); без него порог пересчитывается по фиту, как в `run`.
    #[arg(long)]
    pub xd: Option<PathBuf>,
    /// Окно признаков направления (`z_f1…z_f10`, `r_w_bps`): индекс в `features.windows_min`; по умолчанию — окно 60 мин.
    #[arg(long)]
    pub sign_window: Option<usize>,
    /// Первые N контрактов по алфавиту (замеры); пишется в meta.
    #[arg(long)]
    pub limit: Option<usize>,
}

/// Момент отсчёта события: минута `m`, округлённая ВВЕРХ до кратной слоту `SLOT_MIN` (минуты от `GRID0_MS`).
pub fn tf_of(m: i64) -> i64 {
    -((-m).div_euclid(SLOT_MIN)) * SLOT_MIN
}

/// Событие воспламенения контракта (до отбора по периоду стадии и вселенной).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Event {
    /// Минута воспламенения `m` (от `GRID0_MS`).
    pub m: i64,
    /// Момент отсчёта признаков: `tf_of(m)`.
    pub t_f: i64,
    /// Минут подряд без `hv_state` перед `m` (в пределах сетки контракта), не больше `QUIET_CAP_MIN`.
    pub quiet_min: i64,
    /// Максимум NATR (%) за `[m − PREV_MAX_MIN, m − 1]`; `NaN` — ни одного определённого значения.
    pub natr_prev_max: f32,
}

/// События воспламенения контракта: минуты сетки `first_min + i`, `i < len`, где `Play::first_at` (`hv_state` есть,
/// а в предыдущие `L_pre_min` минут не было). Только данные `≤ m`.
pub fn find_events(pl: &Play, first_min: i64, len: usize) -> Vec<Event> {
    let mut out = Vec::new();
    let mut last: Option<usize> = None;
    for i in 0..len {
        let m = first_min + i as i64;
        if !pl.hv_at(m) {
            continue;
        }
        if pl.first_at(m) {
            let quiet = last.map_or(i, |j| i - j - 1) as i64;
            let mut prev = f32::NAN;
            for k in m - PREV_MAX_MIN..m {
                prev = prev.max(pl.natr_at(k));
            }
            out.push(Event {
                m,
                t_f: tf_of(m),
                quiet_min: quiet.min(QUIET_CAP_MIN),
                natr_prev_max: prev,
            });
        }
        last = Some(i);
    }
    out
}

/// Цена `close_ff` минуты `m` определена (есть в сетке контракта, конечна, положительна).
fn has_price(cf: &[f64], first_min: i64, m: i64) -> bool {
    usize::try_from(m - first_min)
        .ok()
        .and_then(|k| cf.get(k))
        .is_some_and(|x| x.is_finite() && *x > 0.0)
}

/// Индекс окна признаков направления в `features.windows_min`: аргумент либо окно `SIGN_WINDOW_DEFAULT_MIN`.
fn sign_window_index(p: &Params, arg: Option<usize>) -> Result<usize> {
    let ws = &p.features.windows_min;
    match arg {
        Some(i) => {
            ensure!(
                i < ws.len(),
                "--sign-window: индекс {i} вне features.windows_min {ws:?}"
            );
            Ok(i)
        }
        None => ws
            .iter()
            .position(|&w| w == SIGN_WINDOW_DEFAULT_MIN)
            .with_context(|| {
                format!(
                    "features.windows_min {ws:?}: нет окна {SIGN_WINDOW_DEFAULT_MIN} мин — задайте --sign-window"
                )
            }),
    }
}

// ---------- проход по контрактам ----------

/// Контекст прогона (общий для потоков, только чтение).
struct Ctx<'a> {
    p: &'a Params,
    cfg: PlayCfg,
    min_turnover: f64,
    lag_days: u32,
    /// Период стадии `[от, до)`, мин, и усечение контрактов (только стадия `fit`).
    pmin: [i64; 2],
    clip: Option<i64>,
    xd: XdTable,
    oi_lag: u32,
    /// `L_quiet` = `onset.L_pre_min`, мин, и индекс окна признаков направления.
    l_quiet: i64,
    fw: usize,
    shared: Shared,
}

/// Счётчики потока (суммируются по потокам; порядок слияния на результат не влияет).
#[derive(Debug, Clone, Default)]
struct Cnt {
    contracts: u64,
    leverage: u64,
    empty: u64,
    no_oi: u64,
    with_events: u64,
    events_stage: u64,
    events_universe: u64,
    events_out_of_universe: u64,
    dropped_no_entry: u64,
    rows: u64,
    incomplete_path: u64,
    /// Записанные события по календарному месяцу `t_f` (как столбец `month`).
    months: BTreeMap<i32, u64>,
    /// Секунды по фазам: чтение, события, куб и эпизоды, строки.
    secs: [f64; 4],
}

impl Cnt {
    fn merge(&mut self, o: &Cnt) {
        self.contracts += o.contracts;
        self.leverage += o.leverage;
        self.empty += o.empty;
        self.no_oi += o.no_oi;
        self.with_events += o.with_events;
        self.events_stage += o.events_stage;
        self.events_universe += o.events_universe;
        self.events_out_of_universe += o.events_out_of_universe;
        self.dropped_no_entry += o.dropped_no_entry;
        self.rows += o.rows;
        self.incomplete_path += o.incomplete_path;
        for (m, n) in &o.months {
            *self.months.entry(*m).or_default() += n;
        }
        for (a, b) in self.secs.iter_mut().zip(o.secs) {
            *a += b;
        }
    }
}

/// Состояние потока: счётчики, тексты строк контрактов `(индекс файла, байты)`, буферы поиска эпизодов.
struct St {
    cnt: Cnt,
    parts: Vec<(usize, Vec<u8>)>,
    finder: Finder,
}

fn process(ctx: &Ctx, st: &mut St, (idx, path, sym): (usize, &Path, &str)) -> Result<()> {
    let t = Instant::now();
    let mut c = load_contract(path)?;
    if let Some(e) = ctx.clip {
        clip_contract(&mut c, e);
    }
    st.cnt.secs[0] += t.elapsed().as_secs_f64();
    match eligible(sym, &c) {
        Elig::Ok => {}
        Elig::Leverage => {
            st.cnt.leverage += 1;
            return Ok(());
        }
        Elig::Empty => {
            st.cnt.empty += 1;
            return Ok(());
        }
        Elig::NoOi => {
            st.cnt.no_oi += 1;
            return Ok(());
        }
    }
    st.cnt.contracts += 1;

    // события, их отбор по периоду стадии, вселенной и наличию цены входа
    let t = Instant::now();
    let univ = Universe::build(&c, ctx.min_turnover, ctx.lag_days);
    let cf = c.close_ff();
    let pl = Play::build(&c, &cf, ctx.cfg, ctx.l_quiet, [f64::NAN; 2]);
    let mut kept: Vec<Event> = Vec::new();
    for ev in find_events(&pl, c.first_min, c.close.len()) {
        if !(ctx.pmin[0]..ctx.pmin[1]).contains(&ev.m) {
            continue;
        }
        st.cnt.events_stage += 1;
        if !univ.min_ok(ev.m) {
            st.cnt.events_out_of_universe += 1;
            continue;
        }
        st.cnt.events_universe += 1;
        if !has_price(&cf, c.first_min, ev.t_f + 1) {
            st.cnt.dropped_no_entry += 1;
            continue;
        }
        kept.push(ev);
    }
    st.cnt.secs[1] += t.elapsed().as_secs_f64();
    if kept.is_empty() {
        return Ok(());
    }
    st.cnt.with_events += 1;

    // куб признаков и эпизоды знака +1 (`ep_running`) — только для контрактов с событиями
    let t = Instant::now();
    let cube = feat::build_cube(&c, ctx.p, ctx.oi_lag);
    let eps = st.finder.find(&c, ctx.p, &ctx.xd, sym);
    let [running_up, _] = running_mask(&cube, &eps);
    st.cnt.secs[2] += t.elapsed().as_secs_f64();

    let t = Instant::now();
    let mut col = Collector::new(&ctx.shared, sym, &c, &cf, &cube)
        .with_play(Some(&pl))
        .with_features(Some(ctx.oi_lag));
    for ev in &kept {
        let running = usize::try_from(ev.t_f.div_euclid(SLOT_MIN) - cube.slot0)
            .ok()
            .and_then(|i| running_up.get(i).copied())
            .unwrap_or(false);
        col.add_ignition(
            IGN_RULE,
            ev.t_f,
            ctx.fw,
            running,
            IgnTail {
                quiet_min: ev.quiet_min,
                natr_prev_max: ev.natr_prev_max,
                m_minus_tf: ev.t_f - ev.m,
            },
        );
        st.cnt.rows += 1;
        *st.cnt
            .months
            .entry(dump::time_cols(ev.t_f).month)
            .or_default() += 1;
        // путь исхода `e…e+PATH_MIN` не лежит целиком в данных: `mfe`, `mae` — `NaN` (событие остаётся)
        let e = usize::try_from(ev.t_f + 1 - c.first_min).unwrap_or(usize::MAX);
        st.cnt.incomplete_path += u64::from(e.saturating_add(dump::PATH_MIN) >= cf.len());
    }
    st.parts.push((idx, col.finish()));
    st.cnt.secs[3] += t.elapsed().as_secs_f64();
    Ok(())
}

// ---------- запуск ----------

pub fn ignitions(a: IgnitionsArgs) -> Result<()> {
    let t_all = Instant::now();
    let text = fs::read_to_string(&a.params).with_context(|| a.params.display().to_string())?;
    let p = Params::from_json(&text)?;
    let uni = p.universe()?.clone();
    let doc: serde_json::Value = serde_json::from_str(&text).context("params.json")?;
    let ref_sym = doc
        .pointer("/episodes/market_stratum/ref_symbol")
        .and_then(|v| v.as_str())
        .context("params.json: нет episodes.market_stratum.ref_symbol")?
        .to_string();
    let mut sha = Sha256::new();
    sha.update(text.as_bytes());
    let params_sha256 = hex(&sha.finalize());
    // порог и окно NATR — те же проверки, что у флагов `run` (`PlayArgs::cfg`)
    let cfg = PlayArgs {
        hv_natr_pct: Some(a.hv_natr_pct),
        hv_n: Some(a.hv_n),
        ..PlayArgs::default()
    }
    .cfg()?
    .context("--hv-natr-pct и --hv-n обязательны")?;
    let fw = sign_window_index(&p, a.sign_window)?;
    let threads = a.threads.max(1);
    let l_quiet = i64::from(p.onset.l_pre_min);
    let nd = p.episodes.d_min.len();

    let (fit, _) = period(&p.split.fit)?;
    let pmin = match a.stage {
        Stage::Fit => fit,
        Stage::Test => period(&p.split.test)?.0,
    };
    let clip = (a.stage == Stage::Fit).then_some(pmin[1]);

    let mut files = sorted(&a.cache, ".pmc")?;
    if let Some(n) = a.limit {
        files.truncate(n);
    }
    ensure!(!files.is_empty(), "в {} нет файлов .pmc", a.cache.display());
    let syms: Vec<String> = files.iter().map(|f| sym_of(f, ".pmc")).collect();
    fs::create_dir_all(&a.out)?;

    // X_D — как в `run`: из `--xd` либо два прохода по фиту
    let t = Instant::now();
    let (xd, xd_source) = match &a.xd {
        Some(f) => (read_xd(f, nd)?.0, "file"),
        None => {
            let universe = (uni.min_quote_turnover_usd_per_day, uni.turnover_lag_days);
            let (x, _) = xd_passes_for(&p, fit, universe, &files, &syms, threads)?;
            (x, "computed")
        }
    };
    let secs_xd = t.elapsed().as_secs_f64();
    ensure!(
        xd.x.iter()
            .all(|r| r.iter().all(|v| v.is_finite() && *v > 0.0)),
        "X_D не определён или не положителен ({xd:?}): выборка фита пуста?"
    );
    let shared = Shared::load(&a.cache.join(format!("{ref_sym}.pmc")), clip)?;
    let ctx = Ctx {
        p: &p,
        cfg,
        min_turnover: uni.min_quote_turnover_usd_per_day,
        lag_days: uni.turnover_lag_days,
        pmin,
        clip,
        xd,
        oi_lag: a.oi_lag,
        l_quiet,
        fw,
        shared,
    };

    let sts = {
        let (ctx, files, syms) = (&ctx, &files, &syms);
        par_map(
            threads,
            files.len(),
            |_| St {
                cnt: Cnt::default(),
                parts: Vec::new(),
                finder: Finder::default(),
            },
            move |st: &mut St, i: usize| {
                process(ctx, st, (i, files[i].as_path(), syms[i].as_str()))
            },
        )?
    };
    let mut cnt = Cnt::default();
    let mut parts: Vec<(usize, Vec<u8>)> = Vec::new();
    for st in sts {
        cnt.merge(&st.cnt);
        parts.extend(st.parts);
    }

    let t = Instant::now();
    let stage = a.stage.name();
    dump::write_header(
        &a.out.join(format!("ignitions-{stage}.csv")),
        &dump::ign_header(),
        &syms,
        parts,
    )?;
    let secs_write = t.elapsed().as_secs_f64();

    let by_month: serde_json::Map<String, serde_json::Value> = cnt
        .months
        .iter()
        .map(|(m, n)| (m.to_string(), serde_json::Value::from(*n)))
        .collect();
    let sign_window_min = p.features.windows_min.get(fw).copied();
    let meta = serde_json::json!({
        "stage": stage,
        "params_sha256": params_sha256,
        "seed": a.seed,
        "oi_lag_slots": a.oi_lag,
        "hv_natr_pct": a.hv_natr_pct,
        "hv_n": a.hv_n,
        "l_quiet_min": l_quiet,
        "sign_window_index": fw,
        "sign_window_min": sign_window_min,
        "rule": {"fam": IGN_RULE.fam, "wi": IGN_RULE.wi, "ti": IGN_RULE.ti},
        "sign": 1,
        "threads": threads,
        "limit": a.limit,
        "files": files.len(),
        "xd_source": xd_source,
        "contracts": cnt.contracts,
        "contracts_with_events": cnt.with_events,
        "skipped": {
            "leverage": cnt.leverage,
            "no_candles": cnt.empty,
            "no_oi": cnt.no_oi,
        },
        "events_in_stage": cnt.events_stage,
        "events_in_universe": cnt.events_universe,
        "events_out_of_universe": cnt.events_out_of_universe,
        "events_dropped_incomplete_path": cnt.dropped_no_entry,
        "events": cnt.rows,
        "events_kept_path_shorter_1440": cnt.incomplete_path,
        "events_by_month": by_month,
        "universe": {
            "min_quote_turnover_usd_per_day": uni.min_quote_turnover_usd_per_day,
            "turnover_lag_days": uni.turnover_lag_days,
        },
        "secs_total": t_all.elapsed().as_secs_f64(),
        "secs_by_phase": {
            "xd": secs_xd,
            "parse": cnt.secs[0],
            "events": cnt.secs[1],
            "cube_episodes": cnt.secs[2],
            "rows": cnt.secs[3],
            "write": secs_write,
        },
        "peak_rss_mb": peak_rss_mb(),
        "git_head": std::env::var("GIT_HEAD").ok(),
        "notes": {
            "event": "hv_state[m] и ложь в каждой минуте [m - L_quiet, m - 1]; решение по данным <= m",
            "stage_and_universe": "стадия по минуте m, вселенная по суткам m; в csv только события во вселенной",
            "t_f": "m, округлённое вверх до кратного 5; признаки по данным <= t_f; вход c[t_f+1]",
            "events_dropped_incomplete_path": "нет цены входа c[t_f+1] (конец данных стадии): событие не пишется",
            "events_kept_path_shorter_1440": "путь e..e+1440 не лежит в данных: mfe/mae NaN, событие пишется",
            "events_by_month": "записанные события по месяцу t_f (столбец month)",
            "lead_m_since_quiet": "минут подряд без hv_state перед m в пределах сетки контракта, не больше 1440",
            "m_minus_tf": "t_f - m (0..4)",
        },
    });
    fs::write(
        a.out.join(format!("ignitions-meta-{stage}.json")),
        serde_json::to_string_pretty(&meta)? + "\n",
    )?;
    println!(
        "ignitions {stage}: контрактов {}, событий в стадии {} (во вселенной {}, вне {}), записано {}, без цены входа {}, {:.1} с",
        cnt.contracts,
        cnt.events_stage,
        cnt.events_universe,
        cnt.events_out_of_universe,
        cnt.rows,
        cnt.dropped_no_entry,
        t_all.elapsed().as_secs_f64()
    );
    Ok(())
}
