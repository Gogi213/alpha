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
//! `session.json` (`started_utc`, `start_hour_utc`, `binlog_files`, …),
//! `<SYMBOL>-<день>[-pN].binlog` на инструмент и часть (таск 19/22, резолвер
//! `super::session_parts_for` — с таска 23 сутки и час старта берутся у
//! **каждой части**, не у каталога: владелец гоняет `lob session` в один
//! `--root` день за днём, и верхний `started_utc` — от последней сессии),
//! `gaps.csv`, `clock.csv`. Подкаталоги без `session.json` или без бинлога
//! запрошенного символа молча пропускаются — это не сессия этого символа,
//! не ошибка.
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

/// Реплеит части одних суток одной сессии в записи уровней — по части на
/// элемент результата (таск 23: у каждой части свой час старта и свой
/// `SessionTally`). Части суток прогоняются подряд одним трекером (таск 22,
/// «части читаются подряд как один поток»); на каждые новые сутки трекер
/// заводится с чистого листа — между сутками реальный разрыв записи,
/// продолжать окно повторов/прогрев через него — не свойство данных, а
/// совпадение по счёту, поэтому не переносится. Книга и `FileReplayer` —
/// заново на каждый файл: каждая часть несёт собственный снапшот в начале,
/// тот же приём, что `mod.rs::replay_symbol_over_configs` уже применяет к
/// частям суток `lob record`.
fn replay_session_day(
    parts: &[super::SessionPart],
    cfg: LevelsConfig,
) -> anyhow::Result<Vec<Vec<LevelRecord>>> {
    let mut tracker = LevelTracker::new(cfg);
    let mut per_part = Vec::with_capacity(parts.len());
    for part in parts {
        let mut records = Vec::new();
        replay_binlog_file_into(&part.path, &mut tracker, &mut records)?;
        per_part.push(records);
    }
    Ok(per_part)
}

/// Один файл-часть через общий трекер; книга и `FileReplayer` — свои.
fn replay_binlog_file_into(
    path: &Path,
    tracker: &mut LevelTracker,
    records: &mut Vec<LevelRecord>,
) -> anyhow::Result<()> {
    let data = std::fs::read(path)
        .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
    let mut reader = Reader::open(&data[..])
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    let mut book = Book::new(header.tick_e9, header.step_e9);
    let mut replayer = FileReplayer::new();
    let mut ups = Vec::new();
    let mut tps = Vec::new();
    'frames: loop {
        let frame = super::parts::read_frame_soft(&mut reader, path)?;
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
                feed_session_frame(&book, tracker, up.cts_ms, records);
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
        feed_session_frame(&book, tracker, up.cts_ms, records);
    }
    Ok(())
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
        // Таск 19/23: резолвер сессии (`<SYMBOL>-<день>[-pN].binlog` с
        // сутками и часом старта на каждую часть) — нет `session.json` или
        // файла для запрошенного символа в этой сессии означает «не сессия
        // этого символа», не ошибку (doc модуля).
        let Ok(parts) = super::session_parts_for(&dir, &args.symbol) else {
            continue;
        };
        let verified = read_verify_marker(&dir.join(format!("verify-{}.status", args.symbol)));
        let dir_name = dir
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        for (_, day_parts) in super::group_parts_by_day(parts) {
            let per_part = if verified {
                replay_session_day(&day_parts, cfg)?
            } else {
                vec![Vec::new(); day_parts.len()]
            };
            // Одна сессия = одна часть: `SessionTally` на часть, сутки и час
            // старта — её собственные. Идентификатор — каталог плюс имя
            // файла части: каталог с несколькими частями (двое суток, две
            // сессии в сутки) даёт столько же различимых сессий.
            for (part, records) in day_parts.iter().zip(&per_part) {
                let stem = part
                    .path
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
                let tally = SessionTally {
                    session_id: format!("{dir_name}/{stem}"),
                    day_utc: part.day_utc.clone(),
                    start_hour_utc: part.start_hour_utc,
                    symbol: args.symbol.clone(),
                    verified,
                    n: records.iter().filter(|r| profile.matches(r)).count() as u64,
                };
                let outcome = sample
                    .observe_session(&args.root, tally, &now)
                    .map_err(|e| anyhow::anyhow!("{e}"))?;
                if let Some(f) = outcome.flag {
                    flag = Some(f.days.join(","));
                }
            }
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
mod tests;
