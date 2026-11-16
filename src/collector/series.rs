//! Поминутный ряд на диске: `<корень>/series-minutes.jsonl`. По строке на завершённую минуту работы процесса — потерянные секунды,
//! поздние записи (по минуте прихода), таймауты по видам; страница здоровья рисует по нему столбики и после рестарта.
//!
//! Строка минуты: `{"t":<сек начала минуты>,"lost_ms":N,"gaps":n,"late":[book,trade,mark,raw],"to":[pong_only,silent]}`.
//! Сумма по шардам: `lost_ms` — шард-миллисекунды без данных (паузы ≥ 3 с, длительность — до первого кадра после, шаг 0,5 с).
//! У бирж с дозапросом сделок (`backfill`) пауза шарда сделок не потеря — сделки возвращает REST, потеря видна по номерам: `lost_ids` — окончательно потерянные номера по минуте пропуска,
//! `pause_ms` — паузы всех шардов, `lost_ms` — только паузы шардов книги и mark (их ничем не восстановить: красные, как раньше).
//! Потеря, итог которой пришёл после записи строки минуты (итог дозапроса, вердикт проверки паузы REST), — отдельная строка той же минуты `{"t":…,"lost_ids_late":n,"lost_ms_late":мс}`: страница добавляет её к минуте.
//! Пауза шарда, закончившаяся после записи строки её минуты начала (многоминутная: строка минуты пишется один раз, до конца паузы), — та же поздняя строка с полями
//! `gaps_late` (число), `lost_ms_late` (мс, что вошли бы в `lost_ms` строки) и `pause_ms_late` (мс всех пауз; только у бирж с `pause_ms` в строке).
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
    /// Из `lost_ms` — паузы шардов сделок.
    pub trade_ms: i64,
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
        let is_trade = super::livepool::kind_of(&m.name) == "trade";
        {
            let l = m.lists();
            for &(t, ms) in &l.gap_spans {
                if (a..b).contains(&t) {
                    r.gaps += 1;
                    r.lost_ms += ms;
                    if is_trade {
                        r.trade_ms += ms;
                    }
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

/// Минут, для которых помнится учтённое в записанной строке: потеря, дошедшая позже, пишется отдельной строкой (3 часа — с запасом на очередь и повторы дозапроса).
const BOOKED_MIN: usize = 180;

/// Что уже учтено в записанной строке минуты (и в её поздних строках).
#[derive(Debug)]
struct Booked {
    minute: i64,
    ids: u64,
    ms: i64,
    /// паузы шардов, начавшиеся в минуте: число, мс в `lost_ms`, мс всех пауз
    gaps: u64,
    gap_lost_ms: i64,
    gap_ms: i64,
}

/// Паузы шардов по минуте начала: (число, мс в `lost_ms` строки, мс всех пауз).
type GapTotals = (u64, i64, i64);

pub struct SeriesWriter {
    path: PathBuf,
    file: Option<File>,
    /// последняя записанная минута (или минута запуска): писать с `last + 1`
    last: i64,
    /// Биржа с поиском пропусков номеров сделок: потеря сделок в ряду — только подтверждённый и не дозапрошенный пропуск номеров, паузы шардов сделок — отдельно (`pause_ms`).
    backfill: Option<Arc<super::backfill::BackfillStats>>,
    /// Биржа без номеров сделок, паузы шардов сделок которой проверяет REST (`pausecheck`): в `lost_ms` — паузы книги и mark и паузы сделок с вердиктом «потеря» / «не проверено».
    pause_check: Option<Arc<super::pausecheck::PauseStats>>,
    booked: std::collections::VecDeque<Booked>,
    /// Счётчики потерь, при которых минуты пересчитаны в последний раз.
    seen: (u64, u64),
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
        let mut w = Self {
            path,
            file,
            last,
            backfill: None,
            pause_check: None,
            // у минуты запуска строки нет, но потеря в ней возможна — поздняя строка её запишет
            booked: std::iter::once(Booked {
                minute: last,
                ids: 0,
                ms: 0,
                gaps: 0,
                gap_lost_ms: 0,
                gap_ms: 0,
            })
            .collect(),
            seen: (0, 0),
        };
        w.line(&json!({"start": now_ms / 1000, "version": version}).to_string());
        w
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Потеря сделок в ряду — по номерам (`BackfillStats`), а не по паузам шардов сделок.
    pub fn set_backfill(&mut self, b: Arc<super::backfill::BackfillStats>) {
        self.backfill = Some(b);
    }

    /// Паузы шардов сделок — по вердикту проверки REST (`PauseStats`): тишина рынка в `lost_ms` не входит.
    pub fn set_pause_check(&mut self, p: Arc<super::pausecheck::PauseStats>) {
        self.pause_check = Some(p);
    }

    fn line(&mut self, s: &str) {
        if let Some(f) = self.file.as_mut() {
            let _ = writeln!(f, "{s}");
        }
    }

    /// Потери минуты по журналам итогов: номера сделок (`lost_ids`, дозапрос) и мс пауз шардов сделок (проверка REST).
    fn losses(&self, minute: i64) -> (u64, i64) {
        let (a, b) = (minute * MIN_MS, (minute + 1) * MIN_MS);
        (
            self.backfill.as_ref().map_or(0, |x| x.lost_between(a, b)),
            self.pause_check
                .as_ref()
                .map_or(0, |x| x.bad_ms_between(a, b)),
        )
    }

    /// В строке минуты есть `pause_ms` (биржа с дозапросом или проверкой REST): в `lost_ms` паузы шардов сделок не входят.
    fn split_pause(&self) -> bool {
        self.backfill.is_some() || self.pause_check.is_some()
    }

    /// Паузы шардов, начавшиеся в минутах `from..=to`, по минуте начала — как их считает `minute_row`. Паузы одного шарда не пересекаются и лежат по началу, поэтому обход с конца кончается на первой более ранней минуте.
    fn gap_totals(
        &self,
        shards: &[Arc<ShardMetrics>],
        from: i64,
        to: i64,
    ) -> std::collections::HashMap<i64, GapTotals> {
        let split = self.split_pause();
        let mut out: std::collections::HashMap<i64, GapTotals> = std::collections::HashMap::new();
        for m in shards {
            let counts_as_loss = !(split && super::livepool::kind_of(&m.name) == "trade");
            let l = m.lists();
            for &(t, ms) in l.gap_spans.iter().rev() {
                let minute = t.div_euclid(MIN_MS);
                if minute < from {
                    break;
                }
                if minute > to {
                    continue;
                }
                let e = out.entry(minute).or_default();
                e.0 += 1;
                if counts_as_loss {
                    e.1 += ms;
                }
                e.2 += ms;
            }
        }
        out
    }

    /// Итог дозапроса, вердикт проверки паузы и конец многоминутной паузы приходят после строки минуты (она пишется один раз, по завершении минуты): разница с учтённым в строке — отдельной строкой той же минуты
    /// `{"t": <минута потери>, "lost_ids_late": n, "lost_ms_late": мс, "gaps_late": n, "pause_ms_late": мс}` (Н1 повторной проверки; паузы шардов — 16.11, происшествие 12:50); страница добавляет её к столбику минуты.
    /// `lost_ms_late` есть в строке паузы всегда (даже 0): прежняя страница узнаёт поздние строки по нему и не заменит ими строку минуты.
    fn late_rows(&mut self, shards: &[Arc<ShardMetrics>]) {
        let now = (
            self.backfill.as_ref().map_or(0, |b| b.lost_events()),
            self.pause_check.as_ref().map_or(0, |p| p.bad_events()),
        );
        let losses_changed = now != self.seen;
        self.seen = now;
        let (Some(first), Some(last)) = (
            self.booked.front().map(|b| b.minute),
            self.booked.back().map(|b| b.minute),
        ) else {
            return;
        };
        let gaps = self.gap_totals(shards, first, last);
        let split = self.split_pause();
        let minutes: Vec<i64> = self.booked.iter().map(|b| b.minute).collect();
        for (i, minute) in minutes.into_iter().enumerate() {
            let (ids, ms) = if losses_changed {
                self.losses(minute)
            } else {
                (0, 0)
            };
            let g = gaps.get(&minute).copied().unwrap_or_default();
            let Some(b) = self.booked.get_mut(i) else {
                continue;
            };
            let (d_ids, d_ms) = (ids.saturating_sub(b.ids), (ms - b.ms).max(0));
            let (d_gaps, d_lost, d_pause) = (
                g.0.saturating_sub(b.gaps),
                (g.1 - b.gap_lost_ms).max(0),
                (g.2 - b.gap_ms).max(0),
            );
            if d_ids == 0 && d_ms == 0 && d_gaps == 0 {
                continue;
            }
            b.ids = b.ids.max(ids);
            b.ms = b.ms.max(ms);
            b.gaps = b.gaps.max(g.0);
            b.gap_lost_ms = b.gap_lost_ms.max(g.1);
            b.gap_ms = b.gap_ms.max(g.2);
            let mut row = serde_json::Map::new();
            row.insert("t".into(), json!(minute * 60));
            if d_ids > 0 {
                row.insert("lost_ids_late".into(), json!(d_ids));
            }
            if d_ms + d_lost > 0 || d_gaps > 0 {
                row.insert("lost_ms_late".into(), json!(d_ms + d_lost));
            }
            if d_gaps > 0 {
                row.insert("gaps_late".into(), json!(d_gaps));
                if split {
                    row.insert("pause_ms_late".into(), json!(d_pause));
                }
            }
            self.line(&serde_json::Value::Object(row).to_string());
        }
    }

    /// Дописать завершённые минуты до `now_ms` (текущая минута неполная — не пишется).
    pub fn flush(&mut self, shards: &[Arc<ShardMetrics>], now_ms: i64) {
        self.late_rows(shards);
        let cur = now_ms.div_euclid(MIN_MS);
        while self.last + 1 < cur {
            self.last += 1;
            let r = minute_row(shards, self.last);
            let (lost_ids, bad_trade_ms) = self.losses(self.last);
            let s = if self.backfill.is_some() || self.pause_check.is_some() {
                // `lost_ms` — паузы шардов книги и mark (дозапросом не восстанавливаются: потеря на всех биржах) и, у проверки REST, паузы сделок с вердиктом «потеря» / «не проверено»;
                // паузы шардов сделок — в `pause_ms` вместе с остальными; потеря сделок у дозапроса — `lost_ids` (минута пропуска — приём последней сделки перед ним, не итог дозапроса)
                let mut row = json!({"t": self.last * 60, "lost_ms": r.lost_ms - r.trade_ms + bad_trade_ms, "pause_ms": r.lost_ms});
                if self.backfill.is_some() {
                    row["lost_ids"] = json!(lost_ids);
                }
                row["gaps"] = json!(r.gaps);
                row["late"] = json!(r.late);
                row["to"] = json!([r.to_alive, r.to_silent]);
                row
            } else {
                json!({"t": self.last * 60, "lost_ms": r.lost_ms, "gaps": r.gaps, "late": r.late, "to": [r.to_alive, r.to_silent]})
            }
            .to_string();
            self.line(&s);
            let split = self.split_pause();
            self.booked.push_back(Booked {
                minute: self.last,
                ids: lost_ids,
                ms: bad_trade_ms,
                gaps: r.gaps,
                gap_lost_ms: if split {
                    r.lost_ms - r.trade_ms
                } else {
                    r.lost_ms
                },
                gap_ms: r.lost_ms,
            });
            while self.booked.len() > BOOKED_MIN {
                self.booked.pop_front();
            }
        }
    }
}
