//! Поминутный ряд на диске: `<корень>/series-minutes.jsonl`. По строке на завершённую минуту работы процесса — потерянные секунды,
//! поздние записи (по минуте прихода), таймауты по видам; страница здоровья рисует по нему столбики и после рестарта.
//!
//! Строка минуты: `{"t":<сек начала минуты>,"lost_ms":N,"gaps":n,"late":[book,trade,mark,raw],"to":[pong_only,silent]}`.
//! Сумма по шардам: `lost_ms` — шард-миллисекунды без данных (паузы ≥ 3 с, длительность — до первого кадра после, шаг 0,5 с).
//! Метка запуска: `{"start":<сек>,"version":"<хеш коммита>"}` — рестарт виден на оси без разбора `events`.
//! Минута без строки — процесса не было (нет журнала). Неполная минута запуска и остановки не пишется.

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::json;

use super::metrics::ShardMetrics;

const MIN_MS: i64 = 60_000;

/// Сводка минуты по всем шардам.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct MinuteRow {
    pub lost_ms: i64,
    pub gaps: u64,
    pub late: [u64; 4],
    pub to_alive: u64,
    pub to_silent: u64,
}

/// Строка минуты `minute` (минуты с начала эпохи): паузы по началу, таймауты по метке, поздние — по минуте прихода.
pub fn minute_row(shards: &[Arc<ShardMetrics>], minute: i64) -> MinuteRow {
    let (a, b) = (minute * MIN_MS, (minute + 1) * MIN_MS);
    let mut r = MinuteRow::default();
    for m in shards {
        {
            let l = m.lists();
            for &(t, ms) in &l.gap_spans {
                if (a..b).contains(&t) {
                    r.gaps += 1;
                    r.lost_ms += ms;
                }
            }
            let n = |dq: &std::collections::VecDeque<i64>| {
                u64::try_from(dq.iter().filter(|&&t| (a..b).contains(&t)).count()).unwrap_or(0)
            };
            let (all, silent) = (n(&l.timeouts), n(&l.timeouts_silent));
            r.to_silent += silent;
            r.to_alive += all.saturating_sub(silent);
        }
        let w = m.writer();
        for (k, acc) in r.late.iter_mut().enumerate() {
            *acc += w.fresh.late_in_minute(k, minute);
        }
    }
    r
}

pub struct SeriesWriter {
    path: PathBuf,
    file: Option<File>,
    /// последняя записанная минута (или минута запуска): писать с `last + 1`
    last: i64,
}

impl SeriesWriter {
    /// Открыть ряд, записать метку запуска. Первая строка — со следующей полной минуты; минуты из прежнего файла не перезаписываются.
    pub fn open(root: &Path, now_ms: i64, version: &str) -> Self {
        let path = root.join("series-minutes.jsonl");
        let mut last = now_ms.div_euclid(MIN_MS);
        if let Ok(f) = File::open(&path) {
            let prev = BufReader::new(f)
                .lines()
                .map_while(Result::ok)
                .filter_map(|l| serde_json::from_str::<serde_json::Value>(&l).ok())
                .filter_map(|v| v.get("t").and_then(serde_json::Value::as_i64))
                .max();
            if let Some(t) = prev {
                last = last.max(t.div_euclid(60));
            }
        }
        let file = std::fs::create_dir_all(root).ok().and_then(|()| {
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .ok()
        });
        let mut w = Self { path, file, last };
        w.line(&json!({"start": now_ms / 1000, "version": version}).to_string());
        w
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn line(&mut self, s: &str) {
        if let Some(f) = self.file.as_mut() {
            let _ = writeln!(f, "{s}");
        }
    }

    /// Дописать завершённые минуты до `now_ms` (текущая минута неполная — не пишется).
    pub fn flush(&mut self, shards: &[Arc<ShardMetrics>], now_ms: i64) {
        let cur = now_ms.div_euclid(MIN_MS);
        while self.last + 1 < cur {
            self.last += 1;
            let r = minute_row(shards, self.last);
            let s = json!({
                "t": self.last * 60, "lost_ms": r.lost_ms, "gaps": r.gaps, "late": r.late, "to": [r.to_alive, r.to_silent],
            })
            .to_string();
            self.line(&s);
        }
    }
}
