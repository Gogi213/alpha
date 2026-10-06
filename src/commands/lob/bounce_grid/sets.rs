//! Наборы фильтров касаний (`--set`, `FilterSet`), их артефакты (`SetPaths`),
//! контекст касания по семи осям (`CTX_AXES`, `Range`, `TouchContext`,
//! `touch_contexts`), режим по минутам (`RegimeDay`, `read_regime_day`) и
//! фильтр касаний в момент касания (`TouchFilter`) — общий для сигналов
//! сетки и замера ёмкости (`lob fill-capacity`). Вынесено из `bounce_grid`
//! при разрезке B3 (ревью 23.09), поведение не менялось.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::book::Side;
use crate::lob::levels::{ApproachRecord, H3Mode, TouchRecord};
use crate::lob::r1::{ArmR1, FLOW_N, R1_UNDEF};

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
    /// T4 (П-02, Г-86, P-90 «огромный завал уже проедается»): доля съедания к
    /// касанию не меньше этого процента — зеркало `eaten_max_pct` (там «не
    /// больше», здесь «не меньше»), для условия входа рынком при сильном
    /// разъедании. Только ключ набора `eaten_min=<%>`.
    pub eaten_min_pct: Option<f64>,
    /// T-35 (Г-85, В-122): фронтран касания не меньше стольких лотов (`frontrun_min=<лоты>`) — сумма лотов той
    /// же стороны строго лучше уровня (`TouchRecord::frontrun_lots`, В-45); порог вместо «да/нет» `frontrun`.
    /// Только ключ набора.
    pub frontrun_min_lots: Option<i64>,
    /// Номинал стены при касании не меньше стольких долларов (`usd_min=`):
    /// ось «размер стены» поверх пола `--h3-usd` (стены ≥ пола — надмножество,
    /// так что ключ — подмножество тех же касаний).
    pub usd_min: Option<f64>,
    /// Г-07 (TK-012): глубина позади стены не меньше стольких процентов её размера (`behind_min=<%>`, целое ≥ 0):
    /// `depth_behind_lots × 100 ≥ pct × size_at_touch` (у подхода — на взводе). Только ключ набора.
    pub behind_min_pct: Option<i64>,
    /// Г-07 (TK-012): уровней той же стороны в стопке не меньше `n` (`stack_min=<n>`, n ≥ 1; `stack_levels`,
    /// у подхода — `stack_levels_at_arm`). Только ключ набора.
    pub stack_min: Option<u32>,
    /// TK-025 (R1): границы колонок пакета R1 на подходе (`r1_<колонка>_min|_max=<целое>`), по
    /// порядку колонок `ArmR1::names()` — порядок в записи `--set` на набор не влияет. Только
    /// ключи набора и только `--signal approach`.
    pub r1: Vec<R1Bound>,
    /// Контекст касания (S4): границы в bps по осям `CTX_AXES` — ход монеты
    /// до касания за 10 мин / 1 ч / 4 ч (`ret10m`, `ret1h`, `ret4h`; знак
    /// абсолютный), медиана пула за 1 ч / 4 ч (`pool1h`, `pool4h`) и биток
    /// (`btc1h`, `btc4h`, промежуточные `btc2h`, `btc3h` — E26); ключи
    /// `<ось>_min=` / `<ось>_max=`. Касание без
    /// значения оси при заданной границе выбывает.
    pub ctx: [Range; CTX_AXES.len()],
}

/// Границы одной колонки R1 (`r1_<колонка>_min|_max`); `col` — номер в `ArmR1::names()`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct R1Bound {
    pub col: usize,
    pub min: Option<i64>,
    pub max: Option<i64>,
}

impl R1Bound {
    /// Значение колонки внутри границ. Нет записи R1 или `R1_UNDEF` — не проходит: «не
    /// определено» не может удовлетворить порогу (как ось контекста без значения).
    pub fn holds(&self, r1: Option<&ArmR1>) -> bool {
        let Some(r1) = r1 else { return false };
        let v = if self.col < FLOW_N {
            r1.flow[self.col]
        } else {
            r1.level[self.col - FLOW_N]
        };
        v != R1_UNDEF && self.min.is_none_or(|m| v >= m) && self.max.is_none_or(|m| v <= m)
    }
}

/// Оси контекста касания — порядок общий для `FilterSet::ctx` и `TouchContext`.
/// `btc2h`/`btc3h` — в конце: индексы прежних осей не сдвигаются.
pub const CTX_AXES: [&str; 9] = [
    "ret10m", "ret1h", "ret4h", "pool1h", "pool4h", "btc1h", "btc4h", "btc2h", "btc3h",
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

/// Режим суток по минутам из `regime.py`:
/// `minute_ms → (pool1h, pool4h, btc1h, btc4h, btc2h, btc3h)`.
pub(crate) type RegimeDay = BTreeMap<i64, [Option<f64>; 6]>;

/// Колонки `btc_ret_2h_bps`/`btc_ret_3h_bps` пишет только `regime.py` после E26: в
/// прежних файлах их нет, и значения — `None`. Если оси нужны наборам (`need_mid`),
/// нет колонки — отказ (иначе все касания молча выбыли бы из набора).
pub(crate) fn read_regime_day(dir: &Path, day: &str, need_mid: bool) -> anyhow::Result<RegimeDay> {
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
    let opt = |name: &str| -> anyhow::Result<Option<usize>> {
        match header.iter().position(|h| h == name) {
            Some(i) => Ok(Some(i)),
            None if need_mid => anyhow::bail!(
                "{}: нет колонки {name} — оси btc2h/btc3h требуют режим, пересчитанный regime.py",
                path.display()
            ),
            None => Ok(None),
        }
    };
    let cols = [
        Some(idx("minute_ms")?),
        Some(idx("pool_ret_1h_bps")?),
        Some(idx("pool_ret_4h_bps")?),
        Some(idx("btc_ret_1h_bps")?),
        Some(idx("btc_ret_4h_bps")?),
        opt("btc_ret_2h_bps")?,
        opt("btc_ret_3h_bps")?,
    ];
    let mut out = RegimeDay::new();
    for rec in r.records() {
        let rec = rec?;
        let minute: i64 = rec
            .get(cols[0].expect("minute_ms обязателен"))
            .ok_or_else(|| anyhow::anyhow!("{}: короткая строка", path.display()))?
            .parse()?;
        let mut vals = [None; 6];
        for (k, c) in cols[1..].iter().enumerate() {
            let Some(c) = c else { continue };
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
                .unwrap_or([None; 6]);
            TouchContext {
                axes: [r[0], r[1], r[2], m[0], m[1], m[2], m[3], m[4], m[5]],
            }
        })
        .collect()
}

impl FilterSet {
    /// Хоть один ключ контекста задан.
    pub(super) fn uses_ctx(&self) -> bool {
        self.ctx.iter().any(|r| r.is_set())
    }

    /// Хоть один ключ пакета R1 (`r1_*`) задан — нужен кэш подходов с колонками R1.
    pub(crate) fn uses_r1(&self) -> bool {
        !self.r1.is_empty()
    }

    /// Хоть один ключ режима (`pool*`/`btc*`) задан — нужен `--regime-from`.
    pub(crate) fn uses_regime(&self) -> bool {
        self.ctx[3..].iter().any(|r| r.is_set())
    }

    /// Хоть один ключ промежуточных осей битка (`btc2h`/`btc3h`) задан — в файлах
    /// режима нужны их колонки (`read_regime_day`, `need_mid`).
    pub(crate) fn uses_btc_mid(&self) -> bool {
        self.ctx[7..].iter().any(|r| r.is_set())
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
            eaten_min_pct: None,
            frontrun_min_lots: None,
            usd_min: None,
            behind_min_pct: None,
            stack_min: None,
            r1: Vec::new(),
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
            eaten_min_pct: None,
            frontrun_min_lots: None,
            usd_min: None,
            behind_min_pct: None,
            stack_min: None,
            r1: Vec::new(),
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
                "behind_min" => {
                    let pct: i64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: behind_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct >= 0,
                        "--set {spec:?}: behind_min={v:?} — целый процент ≥ 0"
                    );
                    set.behind_min_pct = Some(pct);
                }
                "stack_min" => {
                    let n: u32 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: stack_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        n >= 1,
                        "--set {spec:?}: stack_min={v:?} — целое число уровней ≥ 1"
                    );
                    set.stack_min = Some(n);
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
                "eaten_min" => {
                    let pct: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: eaten_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct.is_finite(),
                        "--set {spec:?}: eaten_min={v:?} — процент обязан быть числом"
                    );
                    set.eaten_min_pct = Some(pct);
                }
                "frontrun_min" => {
                    let n: i64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: frontrun_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        n >= 1,
                        "--set {spec:?}: frontrun_min={v:?} — целое число лотов ≥ 1"
                    );
                    set.frontrun_min_lots = Some(n);
                }
                "frontrun" => {
                    anyhow::ensure!(
                        v.is_empty() || v == "1" || v == "true",
                        "--set {spec:?}: frontrun без значения (или =1)"
                    );
                    set.frontrun_only = true;
                }
                // TK-025: `r1_<колонка>_min|_max=<целое>`; раньше `_`, иначе ключ ушёл бы в оси контекста.
                _ if k.starts_with("r1_") => set.r1_key(spec, k, v)?,
                _ => {
                    let (axis, bound) = k
                        .rsplit_once('_')
                        .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: неизвестный ключ {k:?} (age|flow|side|frontrun|frontrun_min|eaten|eaten_min|usd_min|behind_min|stack_min|<ось>_min|<ось>_max)"))?;
                    let i = CTX_AXES.iter().position(|a| *a == axis).ok_or_else(|| {
                        anyhow::anyhow!("--set {spec:?}: неизвестная ось {axis:?} (ret10m|ret1h|ret4h|pool1h|pool4h|btc1h|btc4h|btc2h|btc3h)")
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
        set.r1.sort_unstable_by_key(|b| b.col);
        Ok(set)
    }

    /// Ключ `r1_<колонка>_min|_max=<целое>`: колонка — из `ArmR1::names()`, граница — целое, не
    /// `R1_UNDEF`; `min > max` пустил бы набор молча — отказ.
    fn r1_key(&mut self, spec: &str, k: &str, v: &str) -> anyhow::Result<()> {
        let rest = k.strip_prefix("r1_").unwrap_or(k);
        let (name, bound) = rest.rsplit_once('_').unwrap_or((rest, ""));
        anyhow::ensure!(
            matches!(bound, "min" | "max"),
            "--set {spec:?}: {k:?} — ожидается r1_<колонка>_min или r1_<колонка>_max"
        );
        let col = ArmR1::names().position(|n| n == name).ok_or_else(|| {
            anyhow::anyhow!(
                "--set {spec:?}: неизвестная колонка R1 {name:?} в {k:?} (колонки: {})",
                ArmR1::names().collect::<Vec<_>>().join("|")
            )
        })?;
        let n: i64 = v
            .parse()
            .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
        anyhow::ensure!(
            n != R1_UNDEF,
            "--set {spec:?}: {k}={v:?} — «не определено» порогом быть не может"
        );
        let at = match self.r1.iter().position(|b| b.col == col) {
            Some(i) => i,
            None => {
                self.r1.push(R1Bound {
                    col,
                    min: None,
                    max: None,
                });
                self.r1.len() - 1
            }
        };
        let b = &mut self.r1[at];
        if bound == "min" {
            b.min = Some(n);
        } else {
            b.max = Some(n);
        }
        anyhow::ensure!(
            b.min.zip(b.max).is_none_or(|(lo, hi)| lo <= hi),
            "--set {spec:?}: r1_{name}_min > r1_{name}_max — набор был бы пуст"
        );
        Ok(())
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
    pub(crate) eaten_min_pct: Option<f64>,
    pub(crate) frontrun_min_lots: Option<i64>,
    pub(crate) usd_min: Option<f64>,
    pub(crate) behind_min_pct: Option<i64>,
    pub(crate) stack_min: Option<u32>,
    /// TK-025: границы колонок R1 набора; проверяются `admits_r1` на записи подхода.
    pub(crate) r1: &'a [R1Bound],
    pub(crate) ctx: Option<&'a [TouchContext]>,
    pub(crate) ctx_ranges: [Range; CTX_AXES.len()],
    /// `holds_at_touch(t) == Some(true)` по касаниям суток, посчитано один раз на все наборы (`ALPHA_HOLDS_MEMO=1`).
    pub(crate) holds: Option<&'a [bool]>,
    /// Скаляры касаний суток для `admits` без чтения `TouchRecord` (`ALPHA_ADMIT_SOA=1`, нужен `holds`).
    pub(crate) rows: Option<&'a [AdmitRow]>,
}

/// Поля `TouchRecord`, нужные `TouchFilter::admits`, плотной строкой (~72 Б против ≈300 Б записи).
#[derive(Debug, Clone, Copy)]
pub(crate) struct AdmitRow {
    frontrun_some: bool,
    side: Side,
    frontrun_lots: i64,
    age_ms: i64,
    flow_1h_lots: i64,
    size_at_touch: i64,
    price_tick: i64,
    depth_behind_lots: i64,
    stack_levels: u32,
    eaten_pct: f64,
}

impl AdmitRow {
    pub(crate) fn of(t: &TouchRecord) -> Self {
        Self {
            frontrun_some: t.frontrun_tick.is_some(),
            side: t.side,
            frontrun_lots: t.frontrun_lots,
            age_ms: t.age_ms(),
            flow_1h_lots: t.flow_1h_lots,
            size_at_touch: t.size_at_touch,
            price_tick: t.price_tick,
            depth_behind_lots: t.depth_behind_lots,
            stack_levels: t.stack_levels,
            eaten_pct: eaten_pct(t),
        }
    }
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
            eaten_min_pct: p.eaten_min_pct,
            frontrun_min_lots: p.frontrun_min_lots,
            usd_min: p.usd_min,
            behind_min_pct: p.behind_min_pct,
            stack_min: p.stack_min,
            r1: p.r1,
            ctx: p.ctx,
            ctx_ranges: p.ctx_ranges,
            holds: p.holds,
            rows: p.rows,
        }
    }

    /// Фильтр набора над касаниями суток с готовым контекстом (`touch_contexts`):
    /// контекст подаётся, только если у набора есть ключи контекста.
    pub(crate) fn from_set(
        set: &'a FilterSet,
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
            eaten_min_pct: set.eaten_min_pct,
            frontrun_min_lots: set.frontrun_min_lots,
            usd_min: set.usd_min,
            behind_min_pct: set.behind_min_pct,
            stack_min: set.stack_min,
            r1: &set.r1,
            ctx: if set.uses_ctx() { Some(ctx) } else { None },
            ctx_ranges: set.ctx,
            holds: None,
            rows: None,
        }
    }

    /// Границы R1 набора на записи подхода `a` (параллельна касаниям суток при
    /// `--signal approach`). Без ключей — всегда да; нет записи или R1 — нет.
    pub(crate) fn admits_r1(&self, a: Option<&ApproachRecord>) -> bool {
        let r1 = a.and_then(|a| a.r1.as_deref());
        self.r1.iter().all(|b| b.holds(r1))
    }

    /// Проходит ли касание `t` с индексом `ti` (индекс — в контекст суток).
    #[allow(clippy::cast_precision_loss)]
    pub(crate) fn admits(&self, ti: usize, t: &TouchRecord) -> bool {
        if let Some(rows) = self.rows {
            return self.admits_row(ti, &rows[ti]);
        }
        if self.frontrun_only && t.frontrun_tick.is_none() {
            return false;
        }
        if self.frontrun_min_lots.is_some_and(|n| t.frontrun_lots < n) {
            return false;
        }
        // База E1 (В-66): плотность обязана держать порог при подходе цены,
        // а не только при рождении — иначе в сетку идут касания
        // «бывших» плотностей. Окно проверено при старте прогона.
        let holds = match self.holds {
            Some(h) => h[ti],
            None => self.mode.holds_at_touch(t) == Some(true),
        };
        if !holds {
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
        // T4 (П-02, Г-86): зеркало предыдущей проверки — съедено МЕНЬШЕ
        // порога не вход (нужен именно сильно проеденный завал).
        if self.eaten_min_pct.is_some_and(|m| eaten_pct(t) < m) {
            return false;
        }
        // Размер стены: номинал при касании ниже порога набора — не вход.
        if self.usd_min.is_some_and(|m| {
            t.price_tick as f64 * self.tick * t.size_at_touch as f64 * self.lot < m
        }) {
            return false;
        }
        // Г-07 (TK-012): позади стены меньше `pct` % её размера — не вход (целые лоты, без f64).
        if self.behind_min_pct.is_some_and(|pct| {
            i128::from(t.depth_behind_lots) * 100 < i128::from(pct) * i128::from(t.size_at_touch)
        }) {
            return false;
        }
        if self.stack_min.is_some_and(|n| t.stack_levels < n) {
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

    /// То же, что `admits` при `holds`, по строке `AdmitRow` (порядок и арифметика те же, результат тот же).
    #[allow(clippy::cast_precision_loss)]
    fn admits_row(&self, ti: usize, r: &AdmitRow) -> bool {
        if self.frontrun_only && !r.frontrun_some {
            return false;
        }
        if self.frontrun_min_lots.is_some_and(|n| r.frontrun_lots < n) {
            return false;
        }
        if !self.holds.is_some_and(|h| h[ti]) {
            return false;
        }
        if self.min_age_ms.is_some_and(|n| r.age_ms < n) {
            return false;
        }
        if let Some(s_min) = self.min_flow_pct {
            let flow_ok = r.flow_1h_lots > 0
                && r.size_at_touch as f64 / r.flow_1h_lots as f64 * 100.0 >= s_min;
            if !flow_ok {
                return false;
            }
        }
        if self.side.is_some_and(|s| r.side != s) {
            return false;
        }
        if self.eaten_max_pct.is_some_and(|m| r.eaten_pct > m) {
            return false;
        }
        if self.eaten_min_pct.is_some_and(|m| r.eaten_pct < m) {
            return false;
        }
        if self.usd_min.is_some_and(|m| {
            r.price_tick as f64 * self.tick * r.size_at_touch as f64 * self.lot < m
        }) {
            return false;
        }
        if self.behind_min_pct.is_some_and(|pct| {
            i128::from(r.depth_behind_lots) * 100 < i128::from(pct) * i128::from(r.size_at_touch)
        }) {
            return false;
        }
        if self.stack_min.is_some_and(|n| r.stack_levels < n) {
            return false;
        }
        if let Some(ctx) = self.ctx {
            let c = &ctx[ti];
            if !self
                .ctx_ranges
                .iter()
                .zip(&c.axes)
                .all(|(rg, v)| rg.holds(*v))
            {
                return false;
            }
        }
        true
    }
}
