//! `RunFlags` (С-06, КТ-6.0): все `ALPHA_*` счёта — один тип, одно место чтения, снимок в манифест прогона.
//!
//! Умолчание (`Default`) = боевой набор скорости (TK-174, бывший `b14flag`): `sig_cache`, `holds_memo`, `admit_soa`,
//! `admit_cache`, `skip_nosignal`, `skip_same` включены при пустом окружении; `ALPHA_X=0` — явное выключение
//! (сверка со старым путём). `from_vars` чистая (тесты), `from_env` читает окружение один раз.

use std::ffi::OsString;

/// Ёмкость кэша сигналов по умолчанию (сигналов на сутки; бывший `ALPHA_SIG_CACHE=3000000` боевого набора).
pub const SIG_CACHE_DEFAULT: usize = 3_000_000;

/// Снимок флагов `ALPHA_*`. Поля `bool` под «`=1`» — включены только значением `1`; под «задан» — любым значением.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunFlags {
    /// `ALPHA_SIG_CACHE=<n>`: ёмкость кэша сигналов; умолчание 3 000 000, `0` — выкл.
    pub sig_cache: Option<usize>,
    /// `ALPHA_DIAG` задан: диагностические счётчики замеров (С-12; бывшие `ALPHA_ATTEMPT_STATS` и `ALPHA_TICK_STATS`).
    pub diag: bool,
    /// `ALPHA_HOLDS_MEMO`: умолчание вкл, `0` — выкл.
    pub holds_memo: bool,
    /// `ALPHA_ADMIT_SOA`: умолчание вкл (нужен `holds_memo`), `0` — выкл.
    pub admit_soa: bool,
    /// `ALPHA_ADMIT_CACHE`: умолчание вкл, `0` — выкл.
    pub admit_cache: bool,
    /// `ALPHA_SKIP_NOSIGNAL`: умолчание вкл, `0` — выкл.
    pub skip_nosignal: bool,
    /// `ALPHA_SKIP_SAME`: умолчание вкл, `0` — выкл.
    pub skip_same: bool,
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
        let on = |n: &str| get(n) != Some("0");
        let num = |n: &str| get(n).and_then(|v| v.parse::<i64>().ok());
        Self {
            sig_cache: match get("ALPHA_SIG_CACHE") {
                None => Some(SIG_CACHE_DEFAULT),
                Some(_) => num("ALPHA_SIG_CACHE")
                    .and_then(|n| usize::try_from(n).ok())
                    .filter(|&n| n > 0),
            },
            diag: set("ALPHA_DIAG"),
            holds_memo: on("ALPHA_HOLDS_MEMO"),
            admit_soa: on("ALPHA_ADMIT_SOA"),
            admit_cache: on("ALPHA_ADMIT_CACHE"),
            skip_nosignal: on("ALPHA_SKIP_NOSIGNAL"),
            skip_same: on("ALPHA_SKIP_SAME"),
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
