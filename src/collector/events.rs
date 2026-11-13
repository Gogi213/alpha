//! Журнал событий шарда: `events-<шард>.jsonl` (подключение, путь, пауза, ошибка, смена входа, остановка).
//!
//! Строка — JSON с `ev`, `ts_ms`, `shard` и полями события; формат тот же, что у Python-ядра (читают разборы и приёмка).
//! Запись не блокирует горячий путь: события редкие, пишутся потоками сессий и шарда под коротким замком.

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::sync::Mutex;

use serde_json::{json, Map, Value};

use super::metrics::Lists;

const DAY_MS: i64 = 24 * 3600 * 1000;

/// Счётчики health из журнала прежних запусков (`events-<шард>.jsonl`): события последних суток возвращаются в скользящие списки, рестарт не обнуляет
/// таймауты, смены пути и входа, переподключения и паузы. Зовётся до первого события нового процесса. Нет файла — списки пусты.
pub fn replay_lists(root: &Path, shard: &str, now_ms: i64) -> Lists {
    let mut l = Lists::default();
    let Ok(f) = File::open(root.join(format!("events-{shard}.jsonl"))) else {
        return l;
    };
    let cut = now_ms - DAY_MS;
    for line in BufReader::new(f).lines().map_while(Result::ok) {
        let Some(i) = line.find("\"ev\":\"") else {
            continue;
        };
        let name = line
            .get(i + 6..)
            .and_then(|r| r.split('"').next())
            .unwrap_or("");
        if !matches!(
            name,
            "timeout" | "gap" | "closed" | "error" | "path_switch" | "entrance_switch"
        ) {
            continue;
        }
        let Ok(v) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let Some(ts) = v.get("ts_ms").and_then(Value::as_i64).filter(|t| *t > cut) else {
            continue;
        };
        match name {
            "timeout" => {
                l.timeouts.push_back(ts);
                // старые события без поля `data_alive` — как «данные живы» (прежняя запись так и читалась)
                if v.get("data_alive").and_then(Value::as_bool) == Some(false) {
                    l.timeouts_silent.push_back(ts);
                }
            }
            "gap" => l.gaps.push_back(ts),
            "closed" | "error" => l.reconnects.push_back(ts),
            "path_switch" => l.path_switches.push_back(ts),
            _ => l.entrance_switches.push_back(ts),
        }
    }
    l
}

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

pub struct EventLog {
    shard: String,
    file: Mutex<Option<File>>,
}

impl EventLog {
    pub fn open(root: &Path, shard: &str) -> Self {
        let file = std::fs::create_dir_all(root).ok().and_then(|()| {
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(root.join(format!("events-{shard}.jsonl")))
                .ok()
        });
        Self {
            shard: shard.to_string(),
            file: Mutex::new(file),
        }
    }

    /// Событие `ev` с дополнительными полями (`fields` — объект JSON или `Null`).
    pub fn emit(&self, ev: &str, fields: Value) {
        let mut m = Map::new();
        m.insert("ev".into(), json!(ev));
        if let Value::Object(extra) = fields {
            m.extend(extra);
        }
        m.insert("ts_ms".into(), json!(now_ms()));
        m.insert("shard".into(), json!(self.shard));
        let mut line = Value::Object(m).to_string();
        line.push('\n');
        if let Some(f) = self
            .file
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_mut()
        {
            let _ = f.write_all(line.as_bytes());
        }
    }
}
