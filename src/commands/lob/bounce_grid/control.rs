//! Г-03 «случайный уровень» (TK-274, П-15 §5 вариант Б, разбор Судьи `docs/research/reviews/tk274-twin-2026-10-16.md`).
//!
//! Двойников строит `lob touches --twin-seeds` (файл `twins-<SYMBOL>.csv` кэша подходов). Здесь они применяются к
//! кэшу подходов суток **до** любого счёта: `random:<seed>` — подход получает цену двойника (поля стены — размер,
//! возраст, сила — прежние: фильтры наборов читают настоящую стену, к двойнику не применяются), `paired:<seed>` —
//! настоящие стены на **тех же** подходах (парность: Δ не смешивает монету). Подход без двойника выпадает в обоих
//! режимах и считается в отчёте `level-control-<SYMBOL>.csv`. Без флага не вызывается — прежние байты.

use std::collections::HashMap;
use std::path::Path;

use super::cache::DayTouches;
use crate::book::Side;

/// Режим контроля уровня (`--level-control`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LevelControl {
    /// Цена входа/стопа/тейка — от двойника.
    Random(u64),
    /// То же, но правила, читающие стену на цене входа, у двойника выключены (П-15 §5; Судья 17.10, TK-274):
    /// отмена входа по гибели стены (порог В-66, `level_floor_qty`) — у двойника стены нет по построению.
    /// Всё остальное (вход/стоп/тейк/дедлайн/ttl, полоса ухода цены, В-68, `prob:3`) — как у `Random`.
    RandomNoWall(u64),
    /// Настоящие стены на подходах, получивших двойника.
    Paired(u64),
}

impl LevelControl {
    pub fn parse(s: &str) -> Result<Self, String> {
        let (kind, rest) = s
            .split_once(':')
            .ok_or_else(|| format!("{s:?}: нужно random:<seed>[:nowall] или paired:<seed>"))?;
        let (seed, suffix) = match rest.split_once(':') {
            Some((seed, suffix)) => (seed, Some(suffix)),
            None => (rest, None),
        };
        let seed: u64 = seed
            .parse()
            .map_err(|_| format!("{s:?}: seed — целое неотрицательное"))?;
        match (kind, suffix) {
            ("random", None) => Ok(Self::Random(seed)),
            ("random", Some("nowall")) => Ok(Self::RandomNoWall(seed)),
            ("paired", None) => Ok(Self::Paired(seed)),
            ("random" | "paired", Some(_)) => Err(format!("{s:?}: суффикс только :nowall у random")),
            _ => Err(format!("{s:?}: режим random или paired")),
        }
    }

    pub(crate) fn seed(self) -> u64 {
        match self {
            Self::Random(s) | Self::RandomNoWall(s) | Self::Paired(s) => s,
        }
    }

    /// Порог «стена снята» (В-66, `--h3-usd`) для плана: у двойника без правил стены его нет — `None`
    /// (`level_floor_qty = 0`, отмена `WallDead` не срабатывает); иначе порог как задан.
    pub(crate) fn wall_floor_usd(control: Option<Self>, h3_usd: Option<f64>) -> Option<f64> {
        match control {
            Some(Self::RandomNoWall(_)) => None,
            _ => h3_usd,
        }
    }

    pub(crate) fn name(self) -> &'static str {
        match self {
            Self::Random(_) => "random",
            Self::RandomNoWall(_) => "random-nowall",
            Self::Paired(_) => "paired",
        }
    }
}

/// Счётчики контроля по символу (все сутки): сколько подходов получили двойника, расстояние двойник↔стена в
/// тиках (медиана, p90) и доля впереди/позади стены (впереди — ближе к чужой лучшей цене).
#[derive(Debug, Default)]
pub(crate) struct ControlStats {
    pub(crate) approaches: u64,
    pub(crate) built: u64,
    pub(crate) not_found: u64,
    dist_ticks: Vec<u64>,
    ahead: u64,
}

impl ControlStats {
    /// Строка отчёта: `symbol,mode,seed,approaches,twins_built,twins_not_found,dist_ticks_median,dist_ticks_p90,
    /// share_ahead,share_behind` (доли пусты, если двойников нет).
    pub(crate) fn csv_row(&mut self, symbol: &str, c: LevelControl) -> [String; 10] {
        self.dist_ticks.sort_unstable();
        let q = |p: f64| {
            let n = self.dist_ticks.len();
            if n == 0 {
                String::new()
            } else {
                let i = ((n - 1) as f64 * p).round() as usize;
                self.dist_ticks[i].to_string()
            }
        };
        let share = |k: u64| {
            if self.built == 0 {
                String::new()
            } else {
                format!("{:.4}", k as f64 / self.built as f64)
            }
        };
        [
            symbol.to_string(),
            c.name().to_string(),
            c.seed().to_string(),
            self.approaches.to_string(),
            self.built.to_string(),
            self.not_found.to_string(),
            q(0.5),
            q(0.9),
            share(self.ahead),
            share(self.built - self.ahead),
        ]
    }
}

pub(crate) const CONTROL_COLUMNS: [&str; 10] = [
    "symbol",
    "mode",
    "seed",
    "approaches",
    "twins_built",
    "twins_not_found",
    "dist_ticks_median",
    "dist_ticks_p90",
    "share_ahead",
    "share_behind",
];

type TwinKey = (bool, i64, i64);

/// Двойники суток для семени: `<dir>/<сутки>/twins-<SYMBOL>.csv`, иначе общий `<dir>/twins-<SYMBOL>.csv`.
/// Значение `None` — «не найдено». Файла нет — `Err`: контроль без двойников считался бы молча как «ничего не нашли».
fn read_twins(
    dir: &Path,
    symbol: &str,
    day: &str,
    seed: u64,
) -> anyhow::Result<HashMap<TwinKey, Option<i64>>> {
    let per_day = dir.join(day).join(format!("twins-{symbol}.csv"));
    let flat = dir.join(format!("twins-{symbol}.csv"));
    let path = if per_day.is_file() {
        per_day
    } else if flat.is_file() {
        flat
    } else {
        anyhow::bail!(
            "{symbol} {day}: файла двойников twins-{symbol}.csv нет в {} (контроль требует `lob touches --twin-seeds`)",
            dir.display()
        );
    };
    let mut rd = csv::Reader::from_path(&path)?;
    let mut out = HashMap::new();
    for rec in rd.records() {
        let rec = rec?;
        let f = |i: usize| {
            rec.get(i)
                .ok_or_else(|| anyhow::anyhow!("{}: мало колонок", path.display()))
        };
        if f(0)? != day || f(4)?.parse::<u64>()? != seed {
            continue;
        }
        let is_bid = match f(1)? {
            "bid" => true,
            "ask" => false,
            s => anyhow::bail!("{}: сторона {s:?}", path.display()),
        };
        let twin = match f(5)? {
            "" => None,
            t => Some(t.parse()?),
        };
        out.insert((is_bid, f(2)?.parse()?, f(3)?.parse()?), twin);
    }
    Ok(out)
}

/// Применяет контроль к суткам из кэша подходов: оставляет подходы с двойником, при `Random` меняет цену стены на
/// цену двойника; заново строит их вид как касания. Подход без записи в файле двойников — `Err` (файл другого прогона).
pub(crate) fn apply(
    control: LevelControl,
    dir: &Path,
    symbol: &str,
    day: &mut DayTouches,
    stats: &mut ControlStats,
) -> anyhow::Result<()> {
    let Some(aps) = day.approaches.take() else {
        anyhow::bail!("--level-control: сутки {} без записей подхода", day.day);
    };
    let twins = read_twins(dir, symbol, &day.day, control.seed())?;
    let mut kept = Vec::with_capacity(aps.len());
    for mut a in aps {
        stats.approaches += 1;
        let is_bid = a.side == Side::Bid;
        let Some(twin) = twins.get(&(is_bid, a.price_tick, a.arm_ms)) else {
            anyhow::bail!(
                "{symbol} {}: у подхода {} {} arm_ms={} нет записи двойника seed={} — twins-файл не от этого кэша подходов",
                day.day,
                if is_bid { "bid" } else { "ask" },
                a.price_tick,
                a.arm_ms,
                control.seed()
            );
        };
        let Some(tick) = *twin else {
            stats.not_found += 1;
            continue;
        };
        stats.built += 1;
        stats.dist_ticks.push(tick.abs_diff(a.price_tick));
        if (is_bid && tick > a.price_tick) || (!is_bid && tick < a.price_tick) {
            stats.ahead += 1;
        }
        if matches!(control, LevelControl::Random(_) | LevelControl::RandomNoWall(_)) {
            a.price_tick = tick;
        }
        kept.push(a);
    }
    day.touches = kept
        .iter()
        .map(crate::commands::lob::backtest::touch_view_of_approach)
        .collect();
    day.rets = vec![[None; 3]; kept.len()];
    day.approaches = Some(kept);
    Ok(())
}
