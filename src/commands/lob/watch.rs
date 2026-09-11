//! `lob watch` — CLI-обёртка счётчика n/G на сессиях для одного вердиктного
//! профиля (таск 07). Сам счётчик и предикат годности суток —
//! `crate::lob::watch::{WatchSample, SessionTally, session_day_eligible}`;
//! этот файл только читает каталог сессий (`lob session`, таск 04) с диска,
//! разбирает `session.json`, читает маркер сверки и считает наблюдения
//! профиля реплеем — сам счётчик про `LevelRecord`/бинлог/файлы не знает.
//!
//! # Раскладка входа
//!
//! `--root <dir>` — каталог, где лежит **по одному подкаталогу на каждую
//! сессию** `lob session --root <dir>/<session_id>` (таск 04): в каждом —
//! `session.json` (`started_utc`, `start_hour_utc`, …), `<SYMBOL>-<день>.binlog`
//! на инструмент (таск 19, резолвер `super::session_binlog_for`), `gaps.csv`,
//! `clock.csv`. Подкаталоги без `session.json` или без бинлога запрошенного
//! символа молча пропускаются — это не сессия этого символа, не ошибка.
//!
//! # Вердикт verify для сессии — файл-маркер таска 07
//!
//! Ни `lob verify` (`commands/lob/verify.rs`), ни `bybit::verify::run_verify`
//! сегодня не пишут артефакт на диск — только печатают сводку. Этот таск
//! вводит маркер, который читает (не пишет — читает) счётчик:
//! `<session_dir>/verify-<SYMBOL>.status`, файл из одного слова. Ровно `ok` —
//! сессия по этому символу прошла сверку целиком; любое другое содержимое
//! или отсутствующий файл — нет (fail-closed, тот же приём, что «ноль
//! проверок — не годно» у старого `day_eligible`). **Пишет** этот файл
//! таск 09 (`pilot-runs`, ближайший потребитель `lob verify` по волне) —
//! здесь зона таска 07 не идёт дальше определения имени/формата и чтения.
//!
//! # Вердиктный профиль
//!
//! `--profile` — id в формате сетки `shortlist::build_profile_grid`.
//! Поддержаны только оси, которые считаются без расстояния до середины в
//! bps (та плоскость требует срезов середины, привязанных к рождению
//! уровня, — вне зоны таска 07, полный перебор по всем осям — таски 10/12):
//! `marginal:side=bid|ask`, `marginal:outcome=eaten|pulled|mixed`,
//! `marginal:repeat=1|2|>=3`, `marginal:instrument=<SYMBOL>`. Запрос
//! `cross:…`/`marginal:size=…`/`marginal:life=…`/`marginal:dist=…`
//! завершается понятной ошибкой, а не тихим нулём.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog::Reader;
use crate::book::{Book, Side};
use crate::bybit::verify::FileReplayer;
use crate::lob::levels::{LevelObs, LevelRecord, LevelTracker, LevelsConfig, Outcome};
use crate::lob::shortlist::{repeat_bucket, REPEAT_LABELS};
use crate::lob::watch::{session_progress_csv_path, SessionTally, WatchSample};

use super::{resolve_h3_mode, H3Args};
use super::{trade_hit_from_record, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS};

// ---------------------------------------------------------------------------
// `lob watch` (таск 07): счётчик n/G на сессиях для одного профиля.
// ---------------------------------------------------------------------------

/// Аргументы `lob watch`: считает `n` и `G` вердиктного профиля через все
/// сессии символа, пишет `progress-<символ>-<профиль>.csv` и выставляет
/// `ready-<символ>-<профиль>.flag`. Markout не вычисляет ни в каком виде:
/// видит только счётчики (`LevelRecord` читается лишь предикатом профиля).
#[derive(Debug, Args)]
pub struct WatchArgs {
    /// Корень: каталог с подкаталогами сессий (см. doc модуля).
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ кандидата пула — один вызов на каждого (см. `PickReport`).
    #[arg(long)]
    pub symbol: String,
    /// Идентификатор вердиктного профиля (см. doc модуля).
    #[arg(long)]
    pub profile: String,
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд,
    /// тот же выбор, что у `levels`).
    #[command(flatten)]
    pub h3: H3Args,
    /// Прогрев в мс, на сессию (каждая сессия реплеится с чистого трекера —
    /// между сессиями реальный разрыв записи, продолжать окно повторов
    /// через него бессмысленно).
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс, на сессию.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Момент выставления флага строкой UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
}

/// Итог `lob watch` для печати диспетчером (`commands/lob/mod.rs::dispatch`).
/// `n`/`g` — счётчик текущего вердиктного профиля (`WatchSample`), не ячеек
/// C1/C2 отменённого дизайна (таск 17 переименовал поля вслед за сносом).
pub struct WatchSummary {
    pub days: usize,
    pub n: u64,
    pub g: u64,
    /// Сутки выборки через запятую, если флаг выставлен этим прогоном.
    pub flag: Option<String>,
    pub progress: PathBuf,
}

// ---------------------------------------------------------------------------
// Вердиктный профиль: только оси без расстояния до середины (см. doc модуля).
// ---------------------------------------------------------------------------

enum ProfileMatcher {
    Side(Side),
    Outcome(Outcome),
    Repeat(&'static str),
    Instrument,
}

impl ProfileMatcher {
    fn parse(profile_id: &str, symbol: &str) -> anyhow::Result<Self> {
        if let Some(v) = profile_id.strip_prefix("marginal:side=") {
            return match v {
                "bid" => Ok(Self::Side(Side::Bid)),
                "ask" => Ok(Self::Side(Side::Ask)),
                other => anyhow::bail!("marginal:side= ждёt bid|ask, получено {other}"),
            };
        }
        if let Some(v) = profile_id.strip_prefix("marginal:outcome=") {
            return match v {
                "eaten" => Ok(Self::Outcome(Outcome::Eaten)),
                "pulled" => Ok(Self::Outcome(Outcome::Pulled)),
                "mixed" => Ok(Self::Outcome(Outcome::Mixed)),
                other => {
                    anyhow::bail!("marginal:outcome= ждёт eaten|pulled|mixed, получено {other}")
                }
            };
        }
        if let Some(v) = profile_id.strip_prefix("marginal:repeat=") {
            return match REPEAT_LABELS.iter().find(|&&l| l == v) {
                Some(&label) => Ok(Self::Repeat(label)),
                None => {
                    anyhow::bail!("marginal:repeat= ждёт одну из {REPEAT_LABELS:?}, получено {v}")
                }
            };
        }
        if let Some(v) = profile_id.strip_prefix("marginal:instrument=") {
            if v != symbol {
                anyhow::bail!("marginal:instrument={v} не совпадает с --symbol {symbol}");
            }
            return Ok(Self::Instrument);
        }
        anyhow::bail!(
            "профиль {profile_id} не поддержан lob watch: расстояние до середины \
             (marginal:dist=/marginal:size=/marginal:life=/cross:) требует срезов mid \
             при рождении уровня — вне зоны таска 07, полный перебор по всем осям — таски 10/12"
        )
    }

    fn matches(&self, rec: &LevelRecord) -> bool {
        match self {
            ProfileMatcher::Side(s) => rec.side == *s,
            ProfileMatcher::Outcome(o) => rec.outcome() == *o,
            ProfileMatcher::Repeat(label) => repeat_bucket(rec.repeat_count) == *label,
            ProfileMatcher::Instrument => true,
        }
    }
}

// ---------------------------------------------------------------------------
// Каталог сессий: чтение `session.json`, маркера сверки, реплей одного файла.
// ---------------------------------------------------------------------------

/// Подмножество полей `session.json` (`lob session`, таск 04), которое нужно
/// счётчику: остальные (`duration_s`, `instruments`, …) serde молча
/// игнорирует — файл читается, а не разбирается заново.
#[derive(Debug, serde::Deserialize)]
struct SessionMetaPeek {
    started_utc: String,
    start_hour_utc: u32,
}

fn read_session_meta(dir: &Path) -> Option<SessionMetaPeek> {
    let text = std::fs::read_to_string(dir.join("session.json")).ok()?;
    serde_json::from_str(&text).ok()
}

/// Маркер сверки сессии (см. doc модуля): ровно `ok` — сессия годна;
/// отсутствие файла или что угодно ещё — нет, без паники и без ошибки.
fn read_verify_marker(path: &Path) -> bool {
    std::fs::read_to_string(path)
        .map(|s| s.trim() == "ok")
        .unwrap_or(false)
}

/// Подкаталоги `--root`, отсортированные по имени: имена сессий — метки
/// времени (`lob session`), поэтому лексикографический порядок совпадает
/// с хронологическим — детерминировано, без часов этого модуля.
fn session_dirs(root: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let entries = std::fs::read_dir(root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", root.display()))?;
    let mut dirs: Vec<PathBuf> = Vec::new();
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога: {e}"))?;
        let path = e.path();
        if path.is_dir() {
            dirs.push(path);
        }
    }
    dirs.sort();
    Ok(dirs)
}

fn feed_session_frame(
    book: &Book,
    tracker: &mut LevelTracker,
    ts_ms: i64,
    out: &mut Vec<LevelRecord>,
) {
    for side in [Side::Bid, Side::Ask] {
        let obs: Vec<LevelObs> = book
            .levels(side)
            .enumerate()
            .map(|(i, (tick, lots))| LevelObs {
                tick,
                size_lots: lots,
                in_top50: i < 50,
            })
            .collect();
        tracker.observe_frame(ts_ms, side, &obs, out);
    }
}

/// Реплеит один файл сессии (не сутки из нескольких файлов, как
/// `mod.rs::replay_symbol` — сессия уже ровно один файл) в записи уровней.
/// Трекер заводится с чистого листа на каждую сессию: между сессиями
/// реальный разрыв записи, продолжать окно повторов/прогрев через него —
/// не свойство данных, а совпадение по счёту, поэтому не переносится.
fn replay_session_binlog(path: &Path, cfg: LevelsConfig) -> anyhow::Result<Vec<LevelRecord>> {
    let data = std::fs::read(path)
        .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
    let mut reader = Reader::open(&data[..])
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    let mut book = Book::new(header.tick_e9, header.step_e9);
    let mut tracker = LevelTracker::new(cfg);
    let mut replayer = FileReplayer::new();
    let mut records = Vec::new();
    let mut ups = Vec::new();
    let mut tps = Vec::new();
    'frames: loop {
        let frame = reader
            .read_frame()
            .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
        let Some(frame_records) = frame else { break };
        for rec in &frame_records {
            ups.clear();
            tps.clear();
            replayer.push_frame(
                std::slice::from_ref(rec),
                header.tick_e9,
                header.step_e9,
                &mut ups,
                &mut tps,
            );
            let hit = trade_hit_from_record(rec);
            for up in &ups {
                if book.apply(up).is_err() {
                    break 'frames;
                }
                feed_session_frame(&book, &mut tracker, up.cts_ms, &mut records);
            }
            if let Some(h) = hit {
                tracker.observe_trade(h);
            }
        }
    }
    let mut tail = Vec::new();
    replayer.finish(&mut tail);
    for up in &tail {
        if book.apply(up).is_err() {
            break;
        }
        feed_session_frame(&book, &mut tracker, up.cts_ms, &mut records);
    }
    Ok(records)
}

// ---------------------------------------------------------------------------
// Прогон `lob watch`.
// ---------------------------------------------------------------------------

/// Считает `n`/`G` вердиктного профиля через все сессии символа под
/// `--root`: по подкаталогу на сессию, годность и итог — тем же кодом, что
/// применяет гейт (`WatchSample`, `crate::lob::watch`). Markout здесь не
/// считается ни в каком виде.
pub fn run_watch(args: &WatchArgs) -> anyhow::Result<WatchSummary> {
    let profile = ProfileMatcher::parse(&args.profile, &args.symbol)?;
    let mode = resolve_h3_mode(&args.root, &args.symbol, args.h3.h3_mode, args.h3.h3_lots)?;
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let mut sample = WatchSample::new(&args.symbol, &args.profile);
    let mut flag: Option<String> = None;
    for dir in session_dirs(&args.root)? {
        let Some(meta) = read_session_meta(&dir) else {
            continue;
        };
        // Таск 19: резолвер сессии (`<SYMBOL>-<день>.binlog`) — нет файла
        // для запрошенного символа в этой сессии означает «не сессия этого
        // символа», не ошибку (doc модуля).
        let Ok(binlog) = super::session_binlog_for(&dir, &args.symbol) else {
            continue;
        };
        let verified = read_verify_marker(&dir.join(format!("verify-{}.status", args.symbol)));
        let n = if verified {
            let records = replay_session_binlog(&binlog, cfg)?;
            records.iter().filter(|r| profile.matches(r)).count() as u64
        } else {
            0
        };
        let session_id = dir
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day_utc = meta.started_utc.get(..10).unwrap_or_default().to_string();
        let tally = SessionTally {
            session_id,
            day_utc,
            start_hour_utc: meta.start_hour_utc,
            symbol: args.symbol.clone(),
            verified,
            n,
        };
        let outcome = sample
            .observe_session(&args.root, tally, &now)
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        if let Some(f) = outcome.flag {
            flag = Some(f.days.join(","));
        }
    }
    Ok(WatchSummary {
        days: sample.days_len(),
        n: sample.n_total(),
        g: sample.g() as u64,
        flag,
        progress: session_progress_csv_path(&args.root, &args.symbol, &args.profile),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::binlog::{Header, Record, Writer};
    use crate::commands::lob::H3ModeArg;

    fn write_session_dir(
        root: &Path,
        session_id: &str,
        symbol: &str,
        started_utc: &str,
        start_hour_utc: u32,
        verified: bool,
        frames: &[Vec<Record>],
    ) -> PathBuf {
        let dir = root.join(session_id);
        std::fs::create_dir_all(&dir).expect("каталог сессии создаётся");
        let json = format!(
            "{{\"started_utc\":\"{started_utc}\",\"start_hour_utc\":{start_hour_utc},\
             \"instruments\":[\"{symbol}\"]}}"
        );
        std::fs::write(dir.join("session.json"), json).expect("session.json пишется");
        let header = Header {
            tick_e9: super::super::test_support::FIX_TICK_E9,
            step_e9: 1_000_000,
            max_records_per_frame: 4096,
        };
        let mut w = Writer::create(Vec::new(), header, 1).expect("заголовок годен");
        for f in frames {
            w.write_frame(f).expect("кадр пишется");
        }
        w.flush().expect("сброс");
        // Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
        // `<SYMBOL>.binlog` — фикстура следует той же раскладке, которую
        // теперь ждёт `session_binlog_for`.
        let day = &started_utc[..10];
        std::fs::write(dir.join(format!("{symbol}-{day}.binlog")), w.into_inner())
            .expect("бинлог пишется");
        if verified {
            std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok")
                .expect("маркер сверки пишется");
        }
        dir
    }

    /// Один уровень `pulled` (снят на 20% максимума — граница правила
    /// 70/20) на биде: подходит под `marginal:outcome=pulled` и
    /// `marginal:side=bid` разом.
    fn pulled_bid_frames() -> Vec<Vec<Record>> {
        super::super::test_support::three_level_frames()
    }

    #[test]
    fn watch_counts_profile_across_sessions_and_skips_unverified() {
        let dir = tempfile::tempdir().expect("песочница");
        let root = dir.path();
        let frames = pulled_bid_frames();
        // Семь годных суток, одна верифицированная сессия каждая:
        // `three_level_frames` даёт два `pulled` на сессию (бид на 100 и
        // аск на 105 — оба ни разу не торговались, оба закрываются на
        // `finish()`) — G=7, n=14 не хватает n>=100 порога, флаг не встаёт
        // (см. отдельный тест на самом счётчике для случая, где порог
        // достигается).
        for d in 1..=7u32 {
            write_session_dir(
                root,
                &format!("2026-05-{d:02}T020000Z"),
                "SOLUSDT",
                &format!("2026-05-{d:02}T02:00:00Z"),
                2,
                true,
                &frames,
            );
        }
        // Восьмая сессия — отдельные сутки, без сверки. Наблюдения в её
        // бинлоге (n=2) не должны попасть в счёт: сессия выброшена целиком.
        write_session_dir(
            root,
            "2026-05-08T050000Z",
            "SOLUSDT",
            "2026-05-08T05:00:00Z",
            5,
            false,
            &frames,
        );
        let args = WatchArgs {
            root: root.to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            profile: "marginal:outcome=pulled".to_string(),
            h3: H3Args {
                h3_mode: H3ModeArg::Percentile,
                h3_lots: Some(1),
            },
            warmup_ms: 0,
            repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
            now_utc: Some("2026-06-01T00:00:00Z".to_string()),
        };
        let summary = run_watch(&args).expect("прогон watch");
        assert_eq!(summary.days, 8, "восемь суток, включая негодные");
        assert_eq!(
            summary.n, 14,
            "по два pulled на каждую из семи годных сессий"
        );
        assert_eq!(summary.g, 7);
        assert!(
            summary.flag.is_none(),
            "n=14 не достигает CONFIRM_MIN_N=100 — не готово"
        );
        let rows = crate::lob::watch::read_session_progress_rows(&summary.progress)
            .expect("прогресс читается");
        assert_eq!(rows.len(), 8);
        let junk = rows
            .iter()
            .find(|r| r.day_utc == "2026-05-08")
            .expect("строка на восьмые сутки есть");
        assert!(
            !junk.eligible,
            "непрошедшая сверку единственная сессия — сутки негодны"
        );
        assert_eq!(junk.n, 0);
        let first = rows
            .iter()
            .find(|r| r.day_utc == "2026-05-01")
            .expect("строка на первые сутки есть");
        assert_eq!(first.session_start_hours_utc, "02");
        assert_eq!(first.n, 2, "два pulled на сессию, см. pulled_bid_frames");
    }

    #[test]
    fn watch_leaves_the_forbidden_horizon_metric_uncomputed() {
        // Критерий приёмки таска 07 — грепом по собственному исходнику и по
        // ядру счётчика: ни один из двух файлов не тянет запрещённую метрику
        // движения середины по имени (Decision 16 гейта 2.1). Литерал собран
        // из частей: спелись целиком — проверка сработала бы сама на себя.
        let forbidden = concat!("mark", "out");
        const SRC: &str = include_str!("watch.rs");
        assert!(
            !SRC.contains(forbidden),
            "lob watch не вычисляет запрещённую метрику ни в каком виде"
        );
        // Только раздел таска 07 (старый C1/C2-код выше в том же файле
        // упоминает эту метрику в прозе как свой собственный отказ от неё —
        // не относится к новому счётчику и не должно триггерить проверку).
        const CORE_SRC: &str = include_str!("../../lob/watch.rs");
        let core_new_section = CORE_SRC
            .split_once("pub struct SessionTally")
            .expect("таск 07 добавил SessionTally в lob/watch.rs")
            .1;
        assert!(
            !core_new_section.contains(forbidden),
            "счётчик n/G не вычисляет запрещённую метрику ни в каком виде"
        );
    }

    #[test]
    fn profile_matcher_rejects_distance_based_profiles_with_a_clear_error() {
        assert!(ProfileMatcher::parse("cross:SOLUSDT|pulled|[0,1)", "SOLUSDT").is_err());
        assert!(ProfileMatcher::parse("marginal:size=[1,2)", "SOLUSDT").is_err());
        assert!(ProfileMatcher::parse("marginal:life=[0,1s)", "SOLUSDT").is_err());
        assert!(ProfileMatcher::parse("marginal:dist=[0,1)", "SOLUSDT").is_err());
        assert!(ProfileMatcher::parse("marginal:side=bid", "SOLUSDT").is_ok());
        assert!(ProfileMatcher::parse("marginal:instrument=SOLUSDT", "SOLUSDT").is_ok());
        assert!(
            ProfileMatcher::parse("marginal:instrument=NEARUSDT", "SOLUSDT").is_err(),
            "инструмент профиля обязан совпасть с --symbol"
        );
    }

    #[test]
    fn session_dirs_skip_entries_without_session_json_or_symbol_binlog() {
        let dir = tempfile::tempdir().expect("песочница");
        let root = dir.path();
        std::fs::create_dir_all(root.join("not-a-session")).unwrap();
        std::fs::write(root.join("stray-file.txt"), "мусор").unwrap();
        write_session_dir(
            root,
            "2026-05-01T020000Z",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
            2,
            true,
            &pulled_bid_frames(),
        );
        let args = WatchArgs {
            root: root.to_path_buf(),
            symbol: "NEARUSDT".to_string(),
            profile: "marginal:side=bid".to_string(),
            h3: H3Args {
                h3_mode: H3ModeArg::Percentile,
                h3_lots: Some(1),
            },
            warmup_ms: 0,
            repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
            now_utc: Some("2026-06-01T00:00:00Z".to_string()),
        };
        // NEARUSDT не писался ни в одной сессии — ни у одного каталога нет
        // NEARUSDT.binlog, значит суток вовсе нет, а не ошибка.
        let summary = run_watch(&args).expect("прогон watch без сессий символа — не ошибка");
        assert_eq!(summary.days, 0);
        assert_eq!(summary.n, 0);
    }
}
