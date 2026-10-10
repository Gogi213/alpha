//! `RunFlags` (С-06, КТ-6.0): все `ALPHA_*` счёта — один тип, одно место чтения, снимок в манифест прогона.
//!
//! Сейчас тип только описывает семантику каждого флага (как её читают места использования) и ничего не заменяет:
//! чтения `std::env::var*("ALPHA_…")` переедут сюда в КТ-6a/6b/6c. Умолчание (`Default`) = все флаги выключены, как
//! при пустом окружении. `from_vars` чистая (тесты), `from_env` читает окружение один раз.

use std::ffi::OsString;

/// Уровень событийных шагов (`ALPHA_EVENT_STEPS`): `1` — при живых заявках, `entry` — только ожидание входа.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum EventSteps {
    #[default]
    Off,
    All,
    Entry,
}

/// Снимок флагов `ALPHA_*`. Поля `bool` под «`=1`» — включены только значением `1`; под «задан» — любым значением.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunFlags {
    /// `ALPHA_DIRECT_FEED=1`.
    pub direct_feed: bool,
    /// `ALPHA_SIG_CACHE=<n>`: ёмкость кэша сигналов, `n > 0`.
    pub sig_cache: Option<usize>,
    /// `ALPHA_ATTEMPT_STATS` задан.
    pub attempt_stats: bool,
    /// `ALPHA_HOLDS_MEMO=1`.
    pub holds_memo: bool,
    /// `ALPHA_ADMIT_SOA=1`.
    pub admit_soa: bool,
    /// `ALPHA_ADMIT_CACHE=1`.
    pub admit_cache: bool,
    /// `ALPHA_SKIP_NOSIGNAL` задан.
    pub skip_nosignal: bool,
    /// `ALPHA_TRIM_ROWS=<bps>`: полоса урезания ленты суток.
    pub trim_rows: Option<i64>,
    /// `ALPHA_TICK_STATS=1`.
    pub tick_stats: bool,
    /// `ALPHA_FAST_HOLD=1`.
    pub fast_hold: bool,
    /// `ALPHA_HOLD_INDEX=1`.
    pub hold_index: bool,
    /// `ALPHA_HOLD_INDEX_CHECK=1`.
    pub hold_index_check: bool,
    /// `ALPHA_SKIP_SAME=1`.
    pub skip_same: bool,
    /// `ALPHA_HORIZON_GROW=1`.
    pub horizon_grow: bool,
    /// `ALPHA_EVENT_STEPS=1|entry`.
    pub event_steps: EventSteps,
    /// `ALPHA_R2_TRACE` задан.
    pub r2_trace: bool,
    /// `ALPHA_APPROACH_BIN=1`.
    pub approach_bin: bool,
    /// `ALPHA_APPROACH_BIN_DIR=<путь>`.
    pub approach_bin_dir: Option<OsString>,
    /// `ALPHA_APPROACH_BIN_LEVEL=<n>`: уровень zstd, пусто — умолчание места записи.
    pub approach_bin_level: Option<i32>,
    /// `ALPHA_TOUCH_BIN=1`.
    pub touch_bin: bool,
    /// `ALPHA_TOUCH_BIN_LEVEL=<n>`.
    pub touch_bin_level: Option<i32>,
    /// `ALPHA_E2E=<путь>`: файл учёта сквозной проверки.
    pub e2e: Option<OsString>,
    /// Все заданные `ALPHA_*` как есть, по имени — для снимка в манифест (в т.ч. не описанные полями).
    raw: Vec<(String, String)>,
}

impl Default for RunFlags {
    fn default() -> Self {
        Self::from_vars(std::iter::empty())
    }
}

impl RunFlags {
    /// Читает окружение процесса.
    pub fn from_env() -> Self {
        Self::from_vars(std::env::vars_os())
    }

    /// Чистый разбор; переменные не `ALPHA_*` игнорируются, повтор имени — последнее значение.
    pub fn from_vars(vars: impl Iterator<Item = (OsString, OsString)>) -> Self {
        let mut raw: Vec<(String, String)> = Vec::new();
        for (k, v) in vars {
            let k = k.to_string_lossy().into_owned();
            if !k.starts_with("ALPHA_") {
                continue;
            }
            let v = v.to_string_lossy().into_owned();
            match raw.iter_mut().find(|(n, _)| *n == k) {
                Some(slot) => slot.1 = v,
                None => raw.push((k, v)),
            }
        }
        raw.sort();
        let get = |n: &str| raw.iter().find(|(k, _)| k == n).map(|(_, v)| v.as_str());
        let set = |n: &str| get(n).is_some();
        let one = |n: &str| get(n) == Some("1");
        let num = |n: &str| get(n).and_then(|v| v.parse::<i64>().ok());
        Self {
            direct_feed: one("ALPHA_DIRECT_FEED"),
            sig_cache: num("ALPHA_SIG_CACHE")
                .and_then(|n| usize::try_from(n).ok())
                .filter(|&n| n > 0),
            attempt_stats: set("ALPHA_ATTEMPT_STATS"),
            holds_memo: one("ALPHA_HOLDS_MEMO"),
            admit_soa: one("ALPHA_ADMIT_SOA"),
            admit_cache: one("ALPHA_ADMIT_CACHE"),
            skip_nosignal: set("ALPHA_SKIP_NOSIGNAL"),
            trim_rows: num("ALPHA_TRIM_ROWS"),
            tick_stats: one("ALPHA_TICK_STATS"),
            fast_hold: one("ALPHA_FAST_HOLD"),
            hold_index: one("ALPHA_HOLD_INDEX"),
            hold_index_check: one("ALPHA_HOLD_INDEX_CHECK"),
            skip_same: one("ALPHA_SKIP_SAME"),
            horizon_grow: one("ALPHA_HORIZON_GROW"),
            event_steps: match get("ALPHA_EVENT_STEPS") {
                Some("1") => EventSteps::All,
                Some("entry") => EventSteps::Entry,
                _ => EventSteps::Off,
            },
            r2_trace: set("ALPHA_R2_TRACE"),
            approach_bin: one("ALPHA_APPROACH_BIN"),
            approach_bin_dir: get("ALPHA_APPROACH_BIN_DIR").map(OsString::from),
            approach_bin_level: num("ALPHA_APPROACH_BIN_LEVEL").and_then(|n| i32::try_from(n).ok()),
            touch_bin: one("ALPHA_TOUCH_BIN"),
            touch_bin_level: num("ALPHA_TOUCH_BIN_LEVEL").and_then(|n| i32::try_from(n).ok()),
            e2e: get("ALPHA_E2E").map(OsString::from),
            raw,
        }
    }

    /// Строки манифеста `env:ALPHA_X=значение` по имени — тот же вид, что пишет `bounce_grid::plan` (С-06).
    pub fn snapshot_lines(&self) -> Vec<String> {
        self.raw
            .iter()
            .map(|(k, v)| format!("env:{k}={v}"))
            .collect()
    }
}

#[cfg(test)]
mod tests;
