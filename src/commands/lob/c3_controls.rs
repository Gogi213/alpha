//! `lob c3-controls` — Г-013 G1 (TK-353, R4.1): контроль C3 «псевдо-стена». Чистая постобработка готовых артефактов:
//! бинлоги суток (посекундные mid → σ240) + кэш касаний `--touches-from` (`approaches-<SYM>.csv`, `walls-<SYM>.csv` от
//! `--wall-log`) + режим BTC 4ч (`--regime-from`). Подбор — `lob::c3::select_controls` (ядро без I/O); `touches`,
//! `touches-project` и `bounce-grid` не затронуты (гейт «байт в байт» по построению).
//!
//! Выход в `--out`: `c3-<SYM>.csv` (пары «стена → контроль») и `c3-summary-<SYM>.json` (доля стен без контроля,
//! счётчики отбраковки, SMD по ковариатам ДО чтения исходов). Ковариаты (одна функция для стены и кандидата, Судья
//! TK-353): `ln σ240` в рождении, `quiet_s` (взвод − конец прошлого захода в полосу `2·arm_dist`; цензура = окно
//! рождение→взвод), мин. расстояние пути в σ. Режим — класс `btc4h` по границам `--regime-edges-bps` (число назначает
//! Судья), сравнивается в `τ'+a_w`. Монета с тиком > 1 бп — «не определена» (в сводке, без пар).

// Офлайн-постобработка суток (не горячий путь): индексы — внутри длин рядов, касты — счётчики в пределах f64.
#![allow(clippy::indexing_slicing, clippy::cast_precision_loss)]

use std::collections::{BTreeMap, HashSet};
use std::path::{Path, PathBuf};

use clap::Args;

use crate::book::Side;
use crate::lob::c3::{
    pooled_sd, select_controls, sigma_from_second_mids, smd, swap_flags, CovIn, DayCtx, Params,
    Span, WallRec, NCOV, N_REJECT, SIGMA_WINDOW_S,
};

use super::bounce_grid::{cached_walls, read_regime_day, BTC4H_AXIS, COIN_AXES};
use super::sync_basis::{forward_fill, read_market};
use super::touches::read_approaches_cached;

const DAY_SECS: usize = 86_400;
const DAY_MS: i64 = 86_400_000;
const NO_REGIME: u8 = u8::MAX;
const REJECT_NAMES: [&str; N_REJECT] = [
    "same_day",
    "regime",
    "no_sigma",
    "caliper",
    "crossed",
    "no_arm",
    "overlap",
    "reused",
    "wall_in_band",
    "nearer_wall",
];
const COV_NAMES: [&str; NCOV] = ["ln_sigma240", "quiet_s", "min_dist_sigma"];

/// Аргументы `lob c3-controls`. Назначаемые числа (caliper, k, seed, окно часа, границы режима) — без умолчаний.
#[derive(Debug, Args)]
pub struct C3ControlsArgs {
    /// Символ монеты.
    #[arg(long)]
    pub symbol: String,
    /// Сутки окна: `YYYY-MM-DD=часть[,часть…]` (части бинлога суток по порядку); повторяется. Реальные стены берутся
    /// из тех же суток, контроли — из других суток списка.
    #[arg(long = "day", required = true)]
    pub days: Vec<String>,
    /// Кэш касаний (`--touches-from`): `<каталог>/<сутки>/approaches-<SYM>.csv` и `walls-<SYM>.csv`.
    #[arg(long)]
    pub cache: PathBuf,
    /// Режим BTC по минутам (`regime.py`): `<каталог>/<сутки>.csv`.
    #[arg(long)]
    pub regime_from: PathBuf,
    /// Границы классов `btc4h`, бп (по возрастанию): класс = число границ ≤ значения.
    #[arg(
        long,
        value_delimiter = ',',
        required = true,
        allow_hyphen_values = true
    )]
    pub regime_edges_bps: Vec<f64>,
    /// Каталог выхода.
    #[arg(long)]
    pub out: PathBuf,
    /// Горизонт `H` исхода, секунды (окна `[τ,t+H]` и `[τ',t'+H]` не пересекаются).
    #[arg(long)]
    pub horizon_s: i64,
    /// Окно часа суток вокруг `τ+a_w`, секунды.
    #[arg(long)]
    pub tod_window_s: i64,
    /// Caliper подбора, SD (метрика Чебышёва по ковариатам).
    #[arg(long)]
    pub caliper_sd: f64,
    /// Контролей на стену.
    #[arg(long)]
    pub k: usize,
    #[arg(long)]
    pub seed: u64,
    /// Сколько секунд после `τ'+a_w` ждать подхода цены к псевдо-уровню.
    #[arg(long)]
    pub max_wait_s: i64,
    /// Шаг перебора `τ'` внутри окна часа, секунды.
    #[arg(long)]
    pub stride_s: usize,
    /// Список событий-«стен» (CSV `arm_ms,side,price_tick`, side = bid|ask): A_B — взводы B1 после фильтров, A_P(S*)
    /// (Судья TK-353, 22.10). Реальной стеной становится только подход кэша с таким ключом; без файла — все подходы.
    #[arg(long)]
    pub events: Option<PathBuf>,
    /// Прежний порядок проверок без пред-отсева по `ln σ` (для гейта «пары байт в байт»); по умолчанию пред-отсев вкл.
    #[arg(long)]
    pub no_prefilter: bool,
    /// «Нулевая синтетика» G1 §8: у пар seed-детерминированно меняются роли (колонка `swap`).
    #[arg(long)]
    pub null_permute: bool,
}

struct DayData {
    day: i32,
    start_ms: i64,
    tick: f64,
    mid: Vec<f64>,
    sigma: Vec<f64>,
    regime: Vec<u8>,
    spans: Vec<Span>,
}

impl DayData {
    fn ctx(&self) -> DayCtx<'_> {
        DayCtx {
            day: self.day,
            start_ms: self.start_ms,
            regime: &self.regime,
            tick: self.tick,
            mid: &self.mid,
            sigma: &self.sigma,
            spans: &self.spans,
        }
    }
}

fn day_start_ms(day: &str) -> anyhow::Result<i64> {
    let d = chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .map_err(|e| anyhow::anyhow!("--day {day:?}: {e}"))?;
    d.and_hms_opt(0, 0, 0)
        .map(|dt| dt.and_utc().timestamp_millis())
        .ok_or_else(|| anyhow::anyhow!("--day {day:?}: полночь не строится"))
}

/// Стены суток из журнала переходов (`walls-<SYM>.csv`): интервалы «стена включена»; открытый — до конца суток.
fn spans_of(cache: &Path, symbol: &str, day: &str, start_ms: i64) -> anyhow::Result<Vec<Span>> {
    let mut open: BTreeMap<(bool, i64), i64> = BTreeMap::new();
    let mut out = Vec::new();
    for ev in cached_walls(cache, symbol, day)? {
        let key = (ev.side == Side::Ask, ev.price_tick);
        if ev.wall {
            open.entry(key).or_insert(ev.ts_ms);
        } else if let Some(from_ms) = open.remove(&key) {
            out.push(Span {
                side: ev.side,
                tick: ev.price_tick,
                from_ms,
                to_ms: ev.ts_ms,
            });
        }
    }
    for ((ask, tick), from_ms) in open {
        out.push(Span {
            side: if ask { Side::Ask } else { Side::Bid },
            tick,
            from_ms,
            to_ms: start_ms + DAY_MS,
        });
    }
    Ok(out)
}

fn load_day(args: &C3ControlsArgs, spec: &str) -> anyhow::Result<(String, DayData)> {
    let (day, parts) = spec
        .split_once('=')
        .ok_or_else(|| anyhow::anyhow!("--day {spec:?}: нужно YYYY-MM-DD=часть[,часть…]"))?;
    let parts: Vec<PathBuf> = parts.split(',').map(PathBuf::from).collect();
    let start_ms = day_start_ms(day)?;
    let market = read_market(&parts)?;
    #[allow(clippy::cast_precision_loss)]
    let tick = market.tick_e9 as f64 * 1e-9;
    let mid: Vec<f64> = forward_fill(&market.mid)
        .into_iter()
        .take(DAY_SECS)
        .collect();
    let sigma = sigma_from_second_mids(&mid, SIGMA_WINDOW_S as usize);
    let regime_day = read_regime_day(&args.regime_from, day, false, false, false)?;
    let slot = BTC4H_AXIS - COIN_AXES;
    let per_minute: Vec<u8> = (0..DAY_SECS / 60)
        .map(|m| {
            let minute_ms = start_ms + m as i64 * 60_000;
            match regime_day
                .get(&minute_ms)
                .and_then(|v| v.get(slot))
                .copied()
                .flatten()
            {
                Some(v) => args.regime_edges_bps.iter().filter(|&&e| v >= e).count() as u8,
                None => NO_REGIME,
            }
        })
        .collect();
    let regime = (0..DAY_SECS).map(|s| per_minute[s / 60]).collect();
    let spans = spans_of(&args.cache, &args.symbol, day, start_ms)?;
    Ok((
        day.to_string(),
        DayData {
            day: i32::try_from(start_ms / DAY_MS)?,
            start_ms,
            tick,
            mid,
            sigma,
            regime,
            spans,
        },
    ))
}

/// Ковариаты пути до взвода — одна функция для стены и кандидата (Судья TK-353 п.1).
fn path_cov(c: &CovIn) -> [f64; NCOV] {
    let d = c.day;
    let sigma0 = d.sigma[c.s0];
    let gap = |s: usize| match c.side {
        Side::Bid => (d.mid[s] - c.level) / c.level * 1e4,
        Side::Ask => (c.level - d.mid[s]) / c.level * 1e4,
    };
    let band = 2.0 * c.arm_dist_bps;
    let mut run_start = c.s_arm;
    while run_start > c.s0 && gap(run_start - 1) <= band {
        run_start -= 1;
    }
    let mut quiet = (c.s_arm - c.s0) as f64;
    let mut j = run_start;
    while j > c.s0 {
        j -= 1;
        if gap(j) <= band {
            quiet = (c.s_arm - j) as f64;
            break;
        }
    }
    let min_gap = (c.s0..=c.s_arm).map(gap).fold(f64::INFINITY, f64::min);
    [sigma0.ln(), quiet, min_gap / sigma0]
}

#[derive(Default)]
struct Skips {
    birth_before_day: u64,
    no_sigma: u64,
    no_regime: u64,
    wrong_side: u64,
    out_of_range: u64,
    not_event: u64,
}

type EventKey = (i64, bool, i64);

/// Файл событий → множество ключей `(arm_ms, ask, price_tick)`.
fn read_events(path: &Path) -> anyhow::Result<HashSet<EventKey>> {
    let mut rd = csv::Reader::from_path(path)?;
    let mut out = HashSet::new();
    for rec in rd.records() {
        let rec = rec?;
        let f = |i: usize| {
            rec.get(i)
                .ok_or_else(|| anyhow::anyhow!("{path:?}: мало колонок"))
        };
        let ask = match f(1)? {
            "ask" => true,
            "bid" => false,
            v => anyhow::bail!("{path:?}: side {v:?}"),
        };
        out.insert((f(0)?.parse()?, ask, f(2)?.parse()?));
    }
    Ok(out)
}

/// Реальные стены суток из кэша подходов: ряды суток → `WallRec` (ковариаты той же `path_cov`).
fn real_walls(
    args: &C3ControlsArgs,
    day: &str,
    d: &DayData,
    events: Option<&HashSet<EventKey>>,
    sk: &mut Skips,
) -> anyhow::Result<Vec<(WallRec, String)>> {
    let csv = args
        .cache
        .join(day)
        .join(format!("approaches-{}.csv", args.symbol));
    let dc = d.ctx();
    let mut out = Vec::new();
    for row in read_approaches_cached(&csv)? {
        let a = &row.approach;
        if events.is_some_and(|e| !e.contains(&(a.arm_ms, a.side == Side::Ask, a.price_tick))) {
            sk.not_event += 1;
            continue;
        }
        let (Ok(s0), Ok(s_arm)) = (
            usize::try_from((a.level_birth_ms - d.start_ms).div_euclid(1000)),
            usize::try_from((a.arm_ms - d.start_ms).div_euclid(1000)),
        ) else {
            sk.birth_before_day += 1;
            continue;
        };
        if s_arm >= DAY_SECS || s0 > s_arm {
            sk.out_of_range += 1;
            continue;
        }
        let (mid0, sigma0) = (d.mid[s0], d.sigma[s0]);
        if !sigma0.is_finite() || !mid0.is_finite() {
            sk.no_sigma += 1;
            continue;
        }
        let regime = d.regime[s_arm];
        if regime == NO_REGIME {
            sk.no_regime += 1;
            continue;
        }
        #[allow(clippy::cast_precision_loss)]
        let level = a.price_tick as f64 * d.tick;
        let off_bps = match a.side {
            Side::Bid => (mid0 - level) / mid0 * 1e4,
            Side::Ask => (level - mid0) / mid0 * 1e4,
        };
        if off_bps <= 0.0 {
            sk.wrong_side += 1;
            continue;
        }
        #[allow(clippy::cast_precision_loss)]
        let arm_dist_bps = a.arm_dist_bps as f64;
        let cov = path_cov(&CovIn {
            day: &dc,
            s0,
            s_arm,
            level,
            side: a.side,
            arm_dist_bps,
        });
        out.push((
            WallRec {
                id: a.arm_ms as u64,
                arm_ms: a.arm_ms,
                age_ms: a.arm_ms - a.level_birth_ms,
                side: a.side,
                arm_dist_bps,
                dist_sigma: off_bps / sigma0,
                regime,
                cov,
            },
            day.to_string(),
        ));
    }
    Ok(out)
}

fn side_name(s: Side) -> &'static str {
    match s {
        Side::Bid => "bid",
        Side::Ask => "ask",
    }
}

/// Запуск: пары и сводка в `--out`; возвращает строку для stdout.
pub fn run_c3_controls(args: &C3ControlsArgs) -> anyhow::Result<String> {
    let mut data = Vec::new();
    for spec in &args.days {
        data.push(load_day(args, spec)?);
    }
    let tick_bps = data
        .iter()
        .filter_map(|(_, d)| {
            let m = d.mid.iter().copied().find(|v| v.is_finite())?;
            Some(d.tick / m * 1e4)
        })
        .fold(0.0f64, f64::max);
    std::fs::create_dir_all(&args.out)?;
    let summary_path = args.out.join(format!("c3-summary-{}.json", args.symbol));
    if tick_bps > 1.0 {
        let j = serde_json::json!({"symbol": args.symbol, "defined": false, "tick_bps": tick_bps});
        std::fs::write(&summary_path, serde_json::to_vec_pretty(&j)?)?;
        return Ok(format!(
            "c3-controls {}: тик {tick_bps:.2} бп > 1 — не определена",
            args.symbol
        ));
    }
    let mut sk = Skips::default();
    let events = args.events.as_deref().map(read_events).transpose()?;
    let mut walls: Vec<(WallRec, String)> = Vec::new();
    for (day, d) in &data {
        walls.extend(real_walls(args, day, d, events.as_ref(), &mut sk)?);
    }
    let wall_recs: Vec<WallRec> = walls.iter().map(|(w, _)| *w).collect();
    let ctxs: Vec<DayCtx> = data.iter().map(|(_, d)| d.ctx()).collect();
    let p = Params {
        horizon_s: args.horizon_s,
        tod_window_s: args.tod_window_s,
        caliper_sd: args.caliper_sd,
        k: args.k,
        seed: args.seed,
        max_wait_s: args.max_wait_s,
        stride_s: args.stride_s,
        prefilter: !args.no_prefilter,
    };
    let (controls, st) = select_controls(&args.symbol, &wall_recs, &ctxs, &p, path_cov);
    let swaps = if args.null_permute {
        swap_flags(controls.len(), args.seed)
    } else {
        vec![false; controls.len()]
    };
    let mut w = csv::Writer::from_path(args.out.join(format!("c3-{}.csv", args.symbol)))?;
    w.write_record([
        "wall_id",
        "wall_day",
        "side",
        "wall_arm_ms",
        "wall_age_ms",
        "swap",
        "ctrl_day",
        "ctrl_tau_ms",
        "ctrl_arm_ms",
        "ctrl_level_tick",
        "ctrl_dist_sd",
    ])?;
    let mut wall_cov: [Vec<f64>; NCOV] = Default::default();
    let mut ctrl_cov: [Vec<f64>; NCOV] = Default::default();
    for (c, swap) in controls.iter().zip(&swaps) {
        let (wr, wday) = &walls[c.wall];
        w.write_record([
            wr.id.to_string(),
            wday.clone(),
            side_name(wr.side).to_string(),
            wr.arm_ms.to_string(),
            wr.age_ms.to_string(),
            u8::from(*swap).to_string(),
            c.day.to_string(),
            c.tau_ms.to_string(),
            c.arm_ms.to_string(),
            c.level_tick.to_string(),
            format!("{:.4}", c.dist),
        ])?;
        let Some((_, d)) = data.iter().find(|(_, d)| d.day == c.day) else {
            continue;
        };
        let (s0, s_arm) = (
            usize::try_from((c.tau_ms - d.start_ms) / 1000)?,
            usize::try_from((c.arm_ms - d.start_ms) / 1000)?,
        );
        #[allow(clippy::cast_precision_loss)]
        let level = c.level_tick as f64 * d.tick;
        let cov = path_cov(&CovIn {
            day: &d.ctx(),
            s0,
            s_arm,
            level,
            side: wr.side,
            arm_dist_bps: wr.arm_dist_bps,
        });
        for j in 0..NCOV {
            wall_cov[j].push(wr.cov[j]);
            ctrl_cov[j].push(cov[j]);
        }
    }
    w.flush()?;
    let rejects: BTreeMap<&str, u64> = REJECT_NAMES.iter().copied().zip(st.rejects).collect();
    let smds: BTreeMap<&str, f64> = (0..NCOV)
        .map(|j| (COV_NAMES[j], smd(&wall_cov[j], &ctrl_cov[j])))
        .collect();
    let mut by_regime: BTreeMap<u8, (u64, u64)> = BTreeMap::new();
    for (i, (wr, _)) in walls.iter().enumerate() {
        let e = by_regime.entry(wr.regime).or_default();
        e.0 += 1;
        e.1 += u64::from(!st.unmatched.contains(&i));
    }
    let n = walls.len();
    let matched = n - st.unmatched.len();
    let j = serde_json::json!({
        "symbol": args.symbol, "defined": true, "tick_bps": tick_bps,
        "walls": n, "walls_matched": matched,
        "unmatched_share": if n == 0 { f64::NAN } else { st.unmatched.len() as f64 / n as f64 },
        "controls": controls.len(), "candidates": st.candidates, "rejects": rejects,
        "pooled_sd": pooled_sd(&wall_recs), "smd_before_outcomes": smds,
        "by_regime_class": by_regime.iter().map(|(r, (a, b))| (r.to_string(), serde_json::json!({"walls": a, "matched": b}))).collect::<BTreeMap<_, _>>(),
        "skipped": {"birth_before_day": sk.birth_before_day, "no_sigma": sk.no_sigma, "no_regime": sk.no_regime,
                    "wrong_side": sk.wrong_side, "out_of_range": sk.out_of_range, "not_event": sk.not_event},
        "params": {"horizon_s": p.horizon_s, "tod_window_s": p.tod_window_s, "caliper_sd": p.caliper_sd, "k": p.k,
                   "seed": p.seed, "max_wait_s": p.max_wait_s, "stride_s": p.stride_s,
                   "regime_edges_bps": args.regime_edges_bps, "null_permute": args.null_permute,
                   "events": args.events.as_ref().map(|p| p.display().to_string()), "prefilter": p.prefilter},
    });
    std::fs::write(&summary_path, serde_json::to_vec_pretty(&j)?)?;
    Ok(format!(
        "c3-controls {}: стен {n}, с контролем {matched}, контролей {}",
        args.symbol,
        controls.len()
    ))
}
