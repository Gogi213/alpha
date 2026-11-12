//! Журнал событий шарда: `events-<шард>.jsonl` (подключение, путь, пауза, ошибка, смена входа, остановка).
//!
//! Строка — JSON с `ev`, `ts_ms`, `shard` и полями события; формат тот же, что у Python-ядра (читают разборы и приёмка).
//! Запись не блокирует горячий путь: события редкие, пишутся потоками сессий и шарда под коротким замком.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;

use serde_json::{json, Map, Value};

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
