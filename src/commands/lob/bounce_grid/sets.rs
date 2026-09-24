//! Наборы фильтров касаний (`--set`, `FilterSet`), их артефакты (`SetPaths`),
//! контекст касания по семи осям (`CTX_AXES`, `Range`, `TouchContext`,
//! `touch_contexts`), режим по минутам (`RegimeDay`, `read_regime_day`) и
//! фильтр касаний в момент касания (`TouchFilter`) — общий для сигналов
//! сетки и замера ёмкости (`lob fill-capacity`). Вынесено из `bounce_grid`
//! при разрезке B3 (ревью 23.09), поведение не менялось.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::book::Side;
use crate::lob::levels::{H3Mode, TouchRecord};

use super::args::{BounceGridArgs, SideArg};
use super::cache::eaten_pct;
use super::drive::DayParams;

/// Артефакты одного набора фильтров.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SetPaths {
    pub name: String,
    pub rounds_path: PathBuf,
    pub forms_path: PathBuf,
}

/// Набор фильтров касаний одной сетки (`--set`, либо флаги команды).
#[derive(Debug, Clone, PartialEq)]
pub struct FilterSet {
    /// Имя подкаталога; пустое — артефакты в `<out-dir>` (без `--set`).
    pub name: String,
    pub frontrun_only: bool,
    pub min_age_secs: Option<i64>,
    pub min_flow_pct: Option<f64>,
    pub side: Option<SideArg>,
    /// Состояние стены при касании (S1 плана по сторонам, [D 33:57] «не
    /// заходить в разъедания»): доля съедания к касанию `100 × (1 −
    /// size_at_touch / size_max_before)` не больше этого процента. Только
    /// ключ набора `eaten=<%>`, флага у команды нет.
    pub eaten_max_pct: Option<f64>,
    /// Номинал стены при касании не меньше стольких долларов (`usd_min=`):
    /// ось «размер стены» поверх пола `--h3-usd` (стены ≥ пола — надмножество,
    /// так что ключ — подмножество тех же касаний).
    pub usd_min: Option<f64>,
    /// Контекст касания (S4): границы в bps по осям `CTX_AXES` — ход монеты
    /// до касания за 10 мин / 1 ч / 4 ч (`ret10m`, `ret1h`, `ret4h`; знак
    /// абсолютный), медиана пула за 1 ч / 4 ч (`pool1h`, `pool4h`) и биток
    /// (`btc1h`, `btc4h`); ключи `<ось>_min=` / `<ось>_max=`. Касание без
    /// значения оси при заданной границе выбывает.
    pub ctx: [Range; CTX_AXES.len()],
}

/// Оси контекста касания — порядок общий для `FilterSet::ctx` и `TouchContext`.
pub const CTX_AXES: [&str; 7] = [
    "ret10m", "ret1h", "ret4h", "pool1h", "pool4h", "btc1h", "btc4h",
];

/// Границы одной оси контекста, bps; `None` — не задана.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Range {
    pub min: Option<f64>,
    pub max: Option<f64>,
}

impl Range {
    pub(crate) fn is_set(self) -> bool {
        self.min.is_some() || self.max.is_some()
    }

    pub(super) fn holds(self, v: Option<f64>) -> bool {
        if !self.is_set() {
            return true;
        }
        match v {
            None => false,
            Some(x) => self.min.is_none_or(|m| x >= m) && self.max.is_none_or(|m| x <= m),
        }
    }
}

/// Контекст одного касания в порядке `CTX_AXES`; `None` — значения нет
/// (окно за началом записи, нет режима на минуту).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct TouchContext {
    pub axes: [Option<f64>; CTX_AXES.len()],
}

/// Режим суток по минутам из `regime.py`: `minute_ms → (pool1h, pool4h, btc1h, btc4h)`.
pub(crate) type RegimeDay = BTreeMap<i64, [Option<f64>; 4]>;

pub(crate) fn read_regime_day(dir: &Path, day: &str) -> anyhow::Result<RegimeDay> {
    let path = dir.join(format!("{day}.csv"));
    let mut r = csv::ReaderBuilder::new()
        .from_path(&path)
        .map_err(|e| anyhow::anyhow!("--regime-from: {}: {e}", path.display()))?;
    let header = r.headers()?.clone();
    let idx = |name: &str| -> anyhow::Result<usize> {
        header
            .iter()
            .position(|h| h == name)
            .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", path.display()))
    };
    let cols = [
        idx("minute_ms")?,
        idx("pool_ret_1h_bps")?,
        idx("pool_ret_4h_bps")?,
        idx("btc_ret_1h_bps")?,
        idx("btc_ret_4h_bps")?,
    ];
    let mut out = RegimeDay::new();
    for rec in r.records() {
        let rec = rec?;
        let minute: i64 = rec
            .get(cols[0])
            .ok_or_else(|| anyhow::anyhow!("{}: короткая строка", path.display()))?
            .parse()?;
        let mut vals = [None; 4];
        for (k, c) in cols[1..].iter().enumerate() {
            let v = rec.get(*c).unwrap_or("");
            vals[k] = if v.is_empty() {
                None
            } else {
                Some(v.parse::<f64>()?)
            };
        }
        out.insert(minute, vals);
    }
    Ok(out)
}

/// Контекст касаний суток: ход монеты — из кэша (`TouchRow::ret_bps`) или из
/// реплея (`pre_touch_return_bps_csv` — те же шесть знаков), режим — по
/// минуте `start_ms` из `RegimeDay` (нет режима — `None`).
pub(crate) fn touch_contexts(
    rets: &[[Option<f64>; 3]],
    touches: &[TouchRecord],
    regime: Option<&RegimeDay>,
) -> Vec<TouchContext> {
    debug_assert_eq!(rets.len(), touches.len());
    touches
        .iter()
        .zip(rets)
        .map(|(t, r)| {
            let minute = t.start_ms - t.start_ms.rem_euclid(60_000);
            let m = regime
                .and_then(|g| g.get(&minute))
                .copied()
                .unwrap_or([None; 4]);
            TouchContext {
                axes: [r[0], r[1], r[2], m[0], m[1], m[2], m[3]],
            }
        })
        .collect()
}

impl FilterSet {
    /// Хоть один ключ контекста задан.
    pub(super) fn uses_ctx(&self) -> bool {
        self.ctx.iter().any(|r| r.is_set())
    }

    /// Хоть один ключ режима (`pool*`/`btc*`) задан — нужен `--regime-from`.
    pub(crate) fn uses_regime(&self) -> bool {
        self.ctx[3..].iter().any(|r| r.is_set())
    }

    /// Хоть один ключ хода **монеты** до сигнала (`ret10m`/`ret1h`/`ret4h`)
    /// задан — их несёт только кэш касаний (`TouchRow::ret_bps`).
    pub(crate) fn uses_ret(&self) -> bool {
        self.ctx[..3].iter().any(|r| r.is_set())
    }

    /// Ключи контекста набора для шапки: `ret1h_min=… pool4h_max=…`.
    pub(super) fn ctx_label(&self) -> String {
        let mut parts = Vec::new();
        for (name, r) in CTX_AXES.iter().zip(&self.ctx) {
            if let Some(v) = r.min {
                parts.push(format!("{name}_min={v}"));
            }
            if let Some(v) = r.max {
                parts.push(format!("{name}_max={v}"));
            }
        }
        if parts.is_empty() {
            "none".to_string()
        } else {
            parts.join(",")
        }
    }

    pub(super) fn from_args(args: &BounceGridArgs) -> Self {
        Self {
            name: String::new(),
            frontrun_only: args.frontrun_only,
            min_age_secs: args.min_age_secs,
            min_flow_pct: args.min_flow_pct,
            side: args.side,
            eaten_max_pct: None,
            usd_min: None,
            ctx: [Range::default(); CTX_AXES.len()],
        }
    }

    /// `<имя>:<k=v,…>` — см. `BounceGridArgs::sets`. Повтор ключа и чужой
    /// ключ — отказ: набор пишется один раз и читается людьми.
    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        let (name, rest) = spec
            .split_once(':')
            .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: ожидается <имя>:<k=v,…>"))?;
        anyhow::ensure!(
            !name.is_empty()
                && name
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
                && name != "."
                && name != "..",
            "--set {spec:?}: имя набора — буквы, цифры, `.`, `_`, `-`"
        );
        let mut set = Self {
            name: name.to_string(),
            frontrun_only: false,
            min_age_secs: None,
            min_flow_pct: None,
            side: None,
            eaten_max_pct: None,
            usd_min: None,
            ctx: [Range::default(); CTX_AXES.len()],
        };
        let mut seen = std::collections::BTreeSet::new();
        for kv in rest.split(',').filter(|s| !s.is_empty()) {
            let (k, v) = kv.split_once('=').unwrap_or((kv, ""));
            anyhow::ensure!(
                seen.insert(k.to_string()),
                "--set {spec:?}: ключ {k} повторяется"
            );
            match k {
                "age" => {
                    set.min_age_secs = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: age={v:?}: {e}"))?,
                    );
                }
                "flow" => {
                    set.min_flow_pct = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: flow={v:?}: {e}"))?,
                    );
                }
                "side" => {
                    set.side = Some(match v {
                        "bid" => SideArg::Bid,
                        "ask" => SideArg::Ask,
                        _ => anyhow::bail!("--set {spec:?}: side={v:?}, ожидается bid|ask"),
                    });
                }
                "usd_min" => {
                    let usd: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: usd_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        usd.is_finite() && usd >= 0.0,
                        "--set {spec:?}: usd_min={v:?} — доллары обязаны быть неотрицательным числом"
                    );
                    set.usd_min = Some(usd);
                }
                "eaten" => {
                    let pct: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: eaten={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct.is_finite(),
                        "--set {spec:?}: eaten={v:?} — процент обязан быть числом"
                    );
                    set.eaten_max_pct = Some(pct);
                }
                "frontrun" => {
                    anyhow::ensure!(
                        v.is_empty() || v == "1" || v == "true",
                        "--set {spec:?}: frontrun без значения (или =1)"
                    );
                    set.frontrun_only = true;
                }
                _ => {
                    let (axis, bound) = k
                        .rsplit_once('_')
                        .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: неизвестный ключ {k:?} (age|flow|side|frontrun|eaten|<ось>_min|<ось>_max)"))?;
                    let i = CTX_AXES.iter().position(|a| *a == axis).ok_or_else(|| {
                        anyhow::anyhow!("--set {spec:?}: неизвестная ось {axis:?} (ret10m|ret1h|ret4h|pool1h|pool4h|btc1h|btc4h)")
                    })?;
                    let v: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
                    anyhow::ensure!(
                        v.is_finite(),
                        "--set {spec:?}: {k}={v:?} — bps обязаны быть числом"
                    );
                    match bound {
                        "min" => set.ctx[i].min = Some(v),
                        "max" => set.ctx[i].max = Some(v),
                        _ => anyhow::bail!(
                            "--set {spec:?}: {k:?} — ожидается <ось>_min или <ось>_max"
                        ),
                    }
                }
            }
        }
        Ok(set)
    }
}

/// Фильтр касаний набора в момент касания — один и тот же для сигналов сетки
/// (`signals_for`) и для замера ёмкости (`lob fill-capacity`, S10): порог
/// плотности, фронтран, возраст, сила «×поток», сторона, съедание, номинал,
/// контекст. Касание, не прошедшее фильтр, — «пропуск» у вызывающего.
pub(crate) struct TouchFilter<'a> {
    pub(crate) tick: f64,
    pub(crate) lot: f64,
    pub(crate) frontrun_only: bool,
    pub(crate) mode: H3Mode,
    pub(crate) min_age_ms: Option<i64>,
    pub(crate) min_flow_pct: Option<f64>,
    pub(crate) side: Option<Side>,
    pub(crate) eaten_max_pct: Option<f64>,
    pub(crate) usd_min: Option<f64>,
    pub(crate) ctx: Option<&'a [TouchContext]>,
    pub(crate) ctx_ranges: [Range; CTX_AXES.len()],
}

impl<'a> TouchFilter<'a> {
    pub(super) fn from_day(p: &DayParams<'a>) -> Self {
        Self {
            tick: p.tick,
            lot: p.lot,
            frontrun_only: p.frontrun_only,
            mode: p.mode,
            min_age_ms: p.min_age_ms,
            min_flow_pct: p.min_flow_pct,
            side: p.side,
            eaten_max_pct: p.eaten_max_pct,
            usd_min: p.usd_min,
            ctx: p.ctx,
            ctx_ranges: p.ctx_ranges,
        }
    }

    /// Фильтр набора над касаниями суток с готовым контекстом (`touch_contexts`):
    /// контекст подаётся, только если у набора есть ключи контекста.
    pub(crate) fn from_set(
        set: &FilterSet,
        mode: H3Mode,
        tick: f64,
        lot: f64,
        ctx: &'a [TouchContext],
    ) -> Self {
        Self {
            tick,
            lot,
            frontrun_only: set.frontrun_only,
            mode,
            min_age_ms: set.min_age_secs.map(|s| s.saturating_mul(1_000)),
            min_flow_pct: set.min_flow_pct,
            side: set.side.map(Side::from),
            eaten_max_pct: set.eaten_max_pct,
            usd_min: set.usd_min,
            ctx: if set.uses_ctx() { Some(ctx) } else { None },
            ctx_ranges: set.ctx,
        }
    }

    /// Проходит ли касание `t` с индексом `ti` (индекс — в контекст суток).
    #[allow(clippy::cast_precision_loss)]
    pub(crate) fn admits(&self, ti: usize, t: &TouchRecord) -> bool {
        if self.frontrun_only && t.frontrun_tick.is_none() {
            return false;
        }
        // База E1 (В-66): плотность обязана держать порог при подходе цены,
        // а не только при рождении — иначе в сетку идут касания
        // «бывших» плотностей. Окно проверено при старте прогона.
        if self.mode.holds_at_touch(t) != Some(true) {
            return false;
        }
        if self.min_age_ms.is_some_and(|n| t.age_ms() < n) {
            return false;
        }
        if let Some(s_min) = self.min_flow_pct {
            let flow_ok = t.flow_1h_lots > 0
                && t.size_at_touch as f64 / t.flow_1h_lots as f64 * 100.0 >= s_min;
            if !flow_ok {
                return false;
            }
        }
        // Ось стороны: другая сторона выбывает до форм (как возраст и сила).
        if self.side.is_some_and(|s| t.side != s) {
            return false;
        }
        // Состояние стены: съедена к касанию сильнее порога — не вход.
        if self.eaten_max_pct.is_some_and(|m| eaten_pct(t) > m) {
            return false;
        }
        // Размер стены: номинал при касании ниже порога набора — не вход.
        if self.usd_min.is_some_and(|m| {
            t.price_tick as f64 * self.tick * t.size_at_touch as f64 * self.lot < m
        }) {
            return false;
        }
        // Контекст (S4): ход до касания и режим — вне границ набора или
        // без значения при заданной границе — не вход.
        if let Some(ctx) = self.ctx {
            let c = &ctx[ti];
            if !self
                .ctx_ranges
                .iter()
                .zip(&c.axes)
                .all(|(r, v)| r.holds(*v))
            {
                return false;
            }
        }
        true
    }
}
