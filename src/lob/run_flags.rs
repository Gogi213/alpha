//! `RunFlags` (С-06, КТ-6.0): все `ALPHA_*` счёта — один тип, одно место чтения, снимок в манифест прогона.
//!
//! Только конфигурация и диагностика: флаги скорости b14flag (TK-174) — не env, а код. `from_vars` чистая (тесты), `from_env` читает окружение один раз.

use std::ffi::OsString;

/// Снимок флагов `ALPHA_*`. Поля `bool` под «`=1`» — включены только значением `1`; под «задан» — любым значением.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunFlags {
    /// `ALPHA_DIAG` задан: диагностические счётчики замеров (С-12; бывшие `ALPHA_ATTEMPT_STATS` и `ALPHA_TICK_STATS`).
    pub diag: bool,
    /// `ALPHA_R2_TRACE` задан.
    pub r2_trace: bool,
    /// `ALPHA_APPROACH_BIN_DIR=<путь>`.
    pub approach_bin_dir: Option<OsString>,
    /// `ALPHA_APPROACH_BIN_LEVEL=<n>`: уровень zstd, пусто — умолчание места записи.
    pub approach_bin_level: Option<i32>,
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
        let num = |n: &str| get(n).and_then(|v| v.parse::<i64>().ok());
        Self {
            diag: set("ALPHA_DIAG"),
            r2_trace: set("ALPHA_R2_TRACE"),
            approach_bin_dir: get("ALPHA_APPROACH_BIN_DIR").map(OsString::from),
            approach_bin_level: num("ALPHA_APPROACH_BIN_LEVEL").and_then(|n| i32::try_from(n).ok()),
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
