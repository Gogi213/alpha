//! `lob <подкоманда>` — единственная точка входа для всех чисел отчёта.
//!
//! Десять подкоманд, каждая — свой файл в этой директории:
//! `pick` (0.4), `record` (0.3), `verify` (0.6), `export` (6.1) — тонкая
//! печать поверх уже реализованных модулей (`commands::record`,
//! `bybit::verify`, `lob::export`); `clock` (0.5), `probe` (6.2),
//! `levels` (1.1, 1.2), `markout` (2.1), `watch` (4.1), `pilot` (3.1) —
//! тонкие обёртки поверх уже протестированной логики своих модулей: только
//! CLI-аргументы, печать артефакта и коды выхода, бизнес-логики нет.
//!
//! # Разрез на файлы (таск 01, механический перенос из бывшего `lob.rs`)
//!
//! Этот файл (`mod.rs`) держит только то, что нужно нескольким подкомандам
//! разом и потому не может лечь ни в одну из них: перечисление
//! `LobCommand`, диспетчер `dispatch`, реплей суточных файлов в уровни и
//! срезы середины (`replay_symbol` и его составляющие — делят `levels`,
//! `markout`, `watch`, `pilot`), сбор `DayTally` (`day_tallies` — делят
//! `markout` и `watch`). Всё остальное — в одноимённом с подкомандой файле.
//!
//! # Структура `pick/` и почему она такая
//!
//! Часовой замер живого стакана нельзя прогнать в юнит-тесте — ему нужна
//! сеть и час времени. Поэтому Decision 25 разложен на два слоя, и сам
//! `pick` — каталог-модуль, а не файл: чистые функции правила (`pool`,
//! `coverage`, `depth`, `order_size`, `table`) и сетевая оболочка (`measure`)
//! лежат каждая в своём файле по ответственности, ни один не больше 900
//! строк — см. doc `pick/mod.rs` про разрез на подмодули.
//!
//! - **Чистые функции** (`build_pool`, `coverage_top50_bps`,
//!   `eligible_baskets`/`count_eligible_trials`,
//!   `median_depth_per_level_usd_e9`, `survivors_above_depth_floor`) реализуют
//!   само правило отбора — пул из десяти после трёх исключений, покрытие
//!   топ-50, пригодность корзин, порог глубины, ранжирование — и не делают
//!   ввода-вывода вообще. Они принимают уже готовые данные (метаданные
//!   инструментов, тикеры, измеренную глубину) и проверены тестами
//!   исчерпывающе, в том числе на вырожденных входах.
//! - **Тонкая оболочка** (`measure_one_symbol`, `measure_prefiltered`,
//!   `run_pick`) ходит в сеть и час ждёт: она только собирает вход для чистых
//!   функций и не содержит собственной логики отбора. Она не покрыта тестами
//!   без сети по той же причине, по которой её нельзя устроить в CI, — и это
//!   не пробел, а прямое следствие того, что вся логика уже вынесена наружу.
//!
//! Ревизия пула (заморозка состава, пол `H3`) — предмет таска 08, зона
//! которого уже ограничена каталогом `pick/`.

use std::path::{Path, PathBuf};

use clap::Subcommand;

use crate::binlog::{Reader, Record};
use crate::book::{Book, Side};
use crate::bybit::clock::check_rows;
use crate::bybit::verify::FileReplayer;
use crate::bybit::verify_sidecar::{read_verify_rows, verify_csv_path, VerifyVerdict};
use crate::commands::record::{gaps_csv_path, read_gap_rows, GapKind};
use crate::lob::levels::{
    DeathKind, LevelObs, LevelRecord, LevelTracker, LevelsConfig, Outcome, TradeHit,
};
use crate::lob::markout::MidSample;
use crate::lob::watch::{tally_day, DayTally};
use hftbacktest::types::{LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT};

pub mod clock;
mod export;
pub mod levels;
pub mod markout;
pub mod pick;
pub mod pilot;
pub mod power;
pub mod probe;
mod record;
pub mod session;
mod verify;
pub mod watch;

pub use clock::{run_clock, ClockArgs};
pub use levels::{run_levels, LevelsArgs};
pub use markout::{run_markout, MarkoutArgs};
pub use pick::{run_pick, write_instruments_csv, PickArgs};
pub use pilot::{run_pilot, PilotArgs};
pub use power::{run_power, PowerArgs};
pub use probe::{run_probe, ProbeArgs};
pub use session::{run_session, SessionArgs};
pub use watch::{run_watch, WatchArgs};

// ---------------------------------------------------------------------------
// Шаг 0.9: шесть недостающих подкоманд. Каждая — тонкая обёртка поверх уже
// протестированной логики своего модуля: CLI-аргументы, вызов, печать
// артефакта, код выхода. Новой бизнес-логики здесь нет; единственная склейка —
// реплей суточных файлов в книгу и уровни (цепочка 1.1 → 1.2 → 2.1 тем же
// кодом), которую делят `levels`, `markout`, `watch` и `pilot`.
// Живые режимы `clock`/`probe` ходят в сеть; `--fixture` прогоняет тот же код
// (замер, цикл, сводка, CSV) на сценарном источнике без сети — ядро этих
// команд покрыто на фейке, живые ≥1000 циклов идут вне песочницы.
// ---------------------------------------------------------------------------

/// Прогрев трекера по умолчанию, мс: 60 минут (`[ASSUMPTION H3]`).
pub const DEFAULT_WARMUP_MS: i64 = 3_600_000;
/// Скользящее окно `repeat_count` по умолчанию, мс: час (шаг 1.1).
pub const DEFAULT_REPEAT_WINDOW_MS: i64 = 3_600_000;
/// Минимум зачтённых `pulled`-уровней гейта G0 (шаг 3.1).
pub const G0_MIN_PULLED: u64 = 200;

// ---------------------------------------------------------------------------
// Общий реплей: суточные файлы символа → записи уровней и срезы середины.
// ---------------------------------------------------------------------------

/// Одни сутки UTC после реплея: записи уровней и срезы середины.
struct ReplayDay {
    day: String,
    records: Vec<LevelRecord>,
    mids: Vec<MidSample>,
}

/// Итог реплея символа: сутки плюс счётчики для замера GC (байт на запись).
struct ReplayStats {
    days: Vec<ReplayDay>,
    bytes: u64,
    records: u64,
}

/// Рабочее состояние одних суток: книга и конвертер пересоздаются на каждый
/// файл (каждый начинается со снапшота), трекер живёт все части суток —
/// окно `repeat_count` и прогрев считаются по суткам, а не по частям файла.
struct DayWork {
    day: String,
    records: Vec<LevelRecord>,
    mids: Vec<MidSample>,
    tracker: LevelTracker,
}

fn is_trade_ev(ev: u64) -> bool {
    ev == LOCAL_BUY_TRADE_EVENT || ev == LOCAL_SELL_TRADE_EVENT
}

/// Трейд записи в трейд трекера. Отображение повторяет контракт писателя
/// (`record.rs::stage_trade`: `ev` из стороны агрессора, `ival = 1` —
/// блочная) и читателя (`verify.rs`: блочность из `ival`); своей трактовки
/// битов здесь нет.
fn trade_hit_from_record(rec: &Record) -> Option<TradeHit> {
    if !is_trade_ev(rec.ev) {
        return None;
    }
    Some(TradeHit {
        tick: rec.price_ticks,
        lots: rec.qty_lots,
        aggressor_is_buy: rec.ev == LOCAL_BUY_TRADE_EVENT,
        block: rec.ival != 0,
        exch_ms: rec.exch_ts_ns / 1_000_000,
    })
}

/// Сутки из имени файла `<SYMBOL>-<день>[-pN].binlog`: первые 10 знаков
/// остатка. Формат проверяет позже `watch` (`BadDay`), здесь только нарезка.
fn day_of_filename(prefix: &str, name: &str) -> Option<String> {
    let rest = name.strip_prefix(prefix)?.strip_suffix(".binlog")?;
    if rest.len() < 10 {
        return None;
    }
    Some(rest[..10].to_string())
}

/// Хронологический ключ файла: день, затем часть суток. То же правило, что
/// `export::part_order_key` (голая лексикография ставит `-p2` раньше начала
/// суток): имя после префикса — либо день, либо день с `-pN`.
fn file_order_key(prefix: &str, name: &str) -> (String, u32) {
    let rest = name.strip_prefix(prefix).unwrap_or(name);
    let rest = rest.strip_suffix(".binlog").unwrap_or(rest);
    if let Some(tail) = rest.get(10..) {
        if let Some(num) = tail.strip_prefix("-p") {
            if let Ok(part) = num.parse::<u32>() {
                return (rest[..10].to_string(), part);
            }
        }
    }
    (rest.to_string(), 1)
}

/// Применяет обновление к книге и кормит трекер кадром обеих сторон плюс
/// срезом середины. Вызывается только после успешного `apply`.
fn feed_frames(
    book: &Book,
    tracker: &mut LevelTracker,
    ts_ms: i64,
    out: &mut Vec<LevelRecord>,
    mids: &mut Vec<MidSample>,
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
    if let (Some(bid), Some(ask)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
        mids.push(MidSample {
            ts_ms,
            bid_tick: bid,
            ask_tick: ask,
        });
    }
}

/// Реплей всех суточных файлов символа тем же кодом, что файловый `verify`:
/// `Reader` читает кадры, `FileReplayer` группирует записи в обновления,
/// `Book` их применяет, `LevelTracker` развешивает уровни и трейды.
/// Разрыв последовательности (`Err` из `apply`) останавливает файл, как в
/// `verify`: дальше этот файл недоверен, следующий идёт с чистого листа.
fn replay_symbol(root: &Path, symbol: &str, cfg: LevelsConfig) -> anyhow::Result<ReplayStats> {
    let prefix = format!("{symbol}-");
    let entries = std::fs::read_dir(root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", root.display()))?;
    let mut files: Vec<PathBuf> = Vec::new();
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога: {e}"))?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(&prefix) && name.ends_with(".binlog") {
            files.push(e.path());
        }
    }
    files.sort_by(|a, b| {
        let key = |p: &PathBuf| {
            p.file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        };
        file_order_key(&prefix, &key(a)).cmp(&file_order_key(&prefix, &key(b)))
    });
    if files.is_empty() {
        anyhow::bail!("нет суточных файлов {prefix}*.binlog в {}", root.display());
    }
    let mut stats = ReplayStats {
        days: Vec::new(),
        bytes: 0,
        records: 0,
    };
    let mut work: Vec<DayWork> = Vec::new();
    for path in &files {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day = day_of_filename(&prefix, &name)
            .ok_or_else(|| anyhow::anyhow!("имя {name} не разбирается как сутки"))?;
        let data = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
        stats.bytes += data.len() as u64;
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let header = reader.header();
        if work.last().is_none_or(|w| w.day != day) {
            work.push(DayWork {
                day,
                records: Vec::new(),
                mids: Vec::new(),
                tracker: LevelTracker::new(cfg),
            });
        }
        let entry = work
            .last_mut()
            .ok_or_else(|| anyhow::anyhow!("рабочий день только что добавлен, а его нет"))?;
        let mut book = Book::new(header.tick_e9, header.step_e9);
        let mut replayer = FileReplayer::new();
        let mut ups = Vec::new();
        let mut tps = Vec::new();
        let mut file_ok = true;
        loop {
            let frame = reader
                .read_frame()
                .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
            let Some(frame_records) = frame else { break };
            stats.records += frame_records.len() as u64;
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
                debug_assert_eq!(
                    tps.len(),
                    usize::from(hit.is_some()),
                    "разметка сделок обязана совпадать с FileReplayer"
                );
                for up in &ups {
                    if book.apply(up).is_err() {
                        file_ok = false;
                        break;
                    }
                    feed_frames(
                        &book,
                        &mut entry.tracker,
                        up.cts_ms,
                        &mut entry.records,
                        &mut entry.mids,
                    );
                }
                if !file_ok {
                    break;
                }
                if let Some(h) = hit {
                    entry.tracker.observe_trade(h);
                }
            }
            if !file_ok {
                break;
            }
        }
        if file_ok {
            let mut tail = Vec::new();
            replayer.finish(&mut tail);
            for up in &tail {
                if book.apply(up).is_err() {
                    break;
                }
                feed_frames(
                    &book,
                    &mut entry.tracker,
                    up.cts_ms,
                    &mut entry.records,
                    &mut entry.mids,
                );
            }
        }
    }
    stats.days = work
        .into_iter()
        .map(|w| ReplayDay {
            day: w.day,
            records: w.records,
            mids: w.mids,
        })
        .collect();
    Ok(stats)
}

fn side_name(side: Side) -> &'static str {
    match side {
        Side::Bid => "bid",
        Side::Ask => "ask",
    }
}

fn outcome_name(outcome: Outcome) -> &'static str {
    match outcome {
        Outcome::Eaten => "eaten",
        Outcome::Pulled => "pulled",
        Outcome::Mixed => "mixed",
    }
}

fn death_name(death: DeathKind) -> &'static str {
    match death {
        DeathKind::BelowFraction => "below_fraction",
        DeathKind::LeftTop => "left_top",
    }
}

fn some_or_empty(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.6}")).unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Счётчики суток для `watch` и подтверждающего `markout`.
// ---------------------------------------------------------------------------

/// Собирает `DayTally` из реплея тем же `tally_day`, что читает G2.
/// Качество суток — из файлов корня: `gaps.csv` (разрыв > 6 часов) и
/// `verify.csv` шага 0.8 (расхождения теста 1). Отсутствующие файлы — ноль
/// строк, а не ошибка: сутки без проверок негодны по правилу `day_eligible`
/// (ноль проверок — не годно), и это честный красный, а не падение команды.
///
/// Правила отображения (консервативные, задокументированы здесь, а не
/// размазаны по вызывающим):
/// - разрыв: любая строка `SequenceGap` этих суток и символа — сутки с
///   разрывом (длительности в строке нет, поэтому любое такое событие суток
///   считается старшим);
/// - verify: знаменатель — строки с решённым сравнением (`Ok`/`Mismatch`),
///   числитель — строки `Mismatch`; `Misaligned`/`RestUnavailable` —
///   неопределённые, как `trades_indeterminate` в `verify`.
fn day_tallies(
    root: &Path,
    symbol: &str,
    days: &[ReplayDay],
    median_lifetime_ms: i64,
) -> anyhow::Result<Vec<DayTally>> {
    let gaps = read_gap_rows(&gaps_csv_path(root)).map_err(|e| anyhow::anyhow!("gaps.csv: {e}"))?;
    let verify_rows =
        read_verify_rows(&verify_csv_path(root)).map_err(|e| anyhow::anyhow!("verify.csv: {e}"))?;
    days.iter()
        .map(|day| {
            let gap = gaps.iter().any(|g| {
                g.symbol == symbol
                    && g.ts_utc.starts_with(&day.day)
                    && matches!(g.kind, GapKind::SequenceGap)
            });
            let mut basis = 0u64;
            let mut violations = 0u64;
            for row in verify_rows
                .iter()
                .filter(|r| r.symbol == symbol && r.ts_utc.starts_with(&day.day))
            {
                match row.verdict {
                    VerifyVerdict::Ok => basis += 1,
                    VerifyVerdict::Mismatch => {
                        basis += 1;
                        violations += 1;
                    }
                    VerifyVerdict::Misaligned | VerifyVerdict::RestUnavailable => {}
                }
            }
            Ok(tally_day(
                symbol,
                &day.day,
                &day.records,
                median_lifetime_ms,
                gap,
                violations,
                basis,
            ))
        })
        .collect()
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Debug, Subcommand)]
pub enum LobCommand {
    /// Отбор инструментов для пилота (шаг 0.4, Decision 25).
    Pick(PickArgs),
    /// Непрерывная запись потока в суточные файлы (шаг 0.3, Decision 7/23).
    Record(crate::commands::record::RecordArgs),
    /// Сверка записанных суток: инварианты и сделки в диапазоне книги (шаг 0.6).
    /// Сверка с REST по u — только живой поток (у файла нет u), см. verify.rs.
    Verify(crate::bybit::verify::VerifyArgs),
    /// Экспорт суток в `npy` для крейта `hftbacktest` (шаг 6.1, Decision 17).
    Export(crate::lob::export::ExportArgs),
    /// Замер смещения часов хоста против NTP и `serverTime` (шаг 0.5).
    Clock(ClockArgs),
    /// Распределение RTT полного цикла post-only ордера (шаг 6.2).
    Probe(ProbeArgs),
    /// Разметка уровней с шестью признаками истории и классом (шаги 1.1, 1.2).
    Levels(LevelsArgs),
    /// Markout уровней на четырёх горизонтах (шаг 2.1).
    Markout(MarkoutArgs),
    /// Счётчик n/G для C2, `progress.csv` и `ready.flag` (шаг 4.1).
    Watch(WatchArgs),
    /// Пилотная цепочка 1.1 -> 1.2 -> 2.1 тем же кодом и вердикт G0 (шаг 3.1).
    Pilot(PilotArgs),
    /// Гейт G-POWER-A: `N`, `SR0`, требуемый Шарп до сбора данных (история 43).
    Power(PowerArgs),
    /// Сессия 5-15 минут по всему пулу одновременно, один `Feed` на
    /// рекордер и (таск 15) на стратегию (таск 04, история 7).
    Session(SessionArgs),
}

/// Диспетчер подкоманд `lob`, подключённый в `main.rs`. Печатает то же, что
/// попадает в коммитимую таблицу, на stdout: строку на кандидата плюс
/// прошедших порог глубины, чтобы `lob pick` был полезен и без последующего
/// чтения CSV.
pub fn dispatch(cmd: LobCommand) -> anyhow::Result<()> {
    match cmd {
        LobCommand::Pick(args) => {
            let report = run_pick(&args)?;
            for row in &report.table {
                println!(
                    "{}\t{}\t{}\t{}",
                    row.symbol,
                    if row.excluded_reason.is_empty() {
                        "POOL"
                    } else {
                        row.excluded_reason.as_str()
                    },
                    row.coverage_top50_bps
                        .map(|c| format!("{c:.3}bps"))
                        .unwrap_or_default(),
                    if row.selected_for_pilot {
                        "SELECTED"
                    } else {
                        ""
                    }
                );
            }
            for m in &report.selected {
                println!(
                    "pilot: {} (бид {} / аск {} USD·1e-9)",
                    m.symbol, m.median_bid_depth_usd_e9, m.median_ask_depth_usd_e9
                );
            }
            Ok(())
        }
        LobCommand::Record(args) => record::print_summary(&args),
        LobCommand::Verify(args) => verify::print_summary(&args),
        LobCommand::Export(args) => export::print_summary(&args),
        LobCommand::Clock(args) => {
            let rows = run_clock(&args)?;
            let violations = check_rows(&rows);
            println!(
                "clock: rows={} violations={} out={}",
                rows.len(),
                violations.len(),
                args.root.join("clock.csv").display()
            );
            for v in &violations {
                println!("clock violation: {v:?}");
            }
            Ok(())
        }
        LobCommand::Probe(args) => {
            let (summary, out) = run_probe(&args)?;
            println!(
                "probe: n={} median_ns={} p95_ns={} out={}",
                summary.n,
                summary.median_ns,
                summary.p95_ns,
                out.display()
            );
            Ok(())
        }
        LobCommand::Levels(args) => {
            let summary = run_levels(&args)?;
            println!(
                "levels: days={} levels={} out={}",
                summary.days,
                summary.levels,
                summary.out.display()
            );
            Ok(())
        }
        LobCommand::Markout(args) => {
            let summary = run_markout(&args)?;
            println!(
                "markout: days={} levels={} out={} {}",
                summary.days,
                summary.levels,
                summary.out.display(),
                summary.horizon_line,
            );
            Ok(())
        }
        LobCommand::Watch(args) => {
            let summary = run_watch(&args)?;
            println!(
                "watch: days={} n_c2={} g_c2={} flag={} out={}",
                summary.days,
                summary.n_c2,
                summary.g_c2,
                summary.flag.as_deref().unwrap_or("not-due"),
                summary.progress.display()
            );
            Ok(())
        }
        LobCommand::Pilot(args) => {
            let summary = run_pilot(&args)?;
            println!(
                "pilot: symbol={} n_pulled={} mean_m_10s_bps={:.3} mean_net_bps={:.3} {} runs={}",
                summary.symbol,
                summary.n_pulled,
                summary.mean_m_10s_bps,
                summary.mean_net_bps,
                summary.verdict,
                summary.runs_out.display()
            );
            Ok(())
        }
        LobCommand::Power(args) => {
            let summary = run_power(&args)?;
            println!(
                "power: n={} sr0={:.4} required_sharpe={:.4} dsr_target={:.2} num_obs={}",
                summary.n,
                summary.sr0,
                summary.required_sharpe,
                summary.dsr_target,
                summary.num_obs
            );
            Ok(())
        }
        LobCommand::Session(args) => {
            let summary = run_session(&args)?;
            println!(
                "session: instruments={} start_hour_utc={} records={} gaps={} clock_samples={} out={}",
                summary.instruments.len(),
                summary.start_hour_utc,
                summary.records_total,
                summary.gaps,
                summary.clock_samples,
                summary.out.display()
            );
            Ok(())
        }
    }
}

// ---------------------------------------------------------------------------
// Общая тестовая фикстура: суточные файлы из кадров записей (Header/Writer),
// используемая тестами `levels`, `markout`, `watch`, `pilot`.
// ---------------------------------------------------------------------------

#[cfg(test)]
pub(crate) mod test_support {
    use super::Record;
    use crate::binlog::{Header, Writer};
    use hftbacktest::types::{
        LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
        LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    };

    pub(crate) const FIX_TICK_E9: i64 = 10_000_000; // 0.01
    const FIX_STEP_E9: i64 = 1_000_000; // 0.001

    fn test_header() -> Header {
        Header {
            tick_e9: FIX_TICK_E9,
            step_e9: FIX_STEP_E9,
            max_records_per_frame: 4096,
        }
    }

    fn depth_rec(ev: u64, ts_ms: i64, tick: i64, lots: i64) -> Record {
        Record {
            ev,
            exch_ts_ns: ts_ms * 1_000_000,
            local_ts_ns: ts_ms * 1_000_000 + 500_000,
            price_ticks: tick,
            qty_lots: lots,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    pub(crate) fn snap_frame(ts_ms: i64, bids: &[(i64, i64)], asks: &[(i64, i64)]) -> Vec<Record> {
        let mut out = Vec::new();
        for &(tick, lots) in bids {
            out.push(depth_rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, ts_ms, tick, lots));
        }
        for &(tick, lots) in asks {
            out.push(depth_rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, ts_ms, tick, lots));
        }
        out
    }

    pub(crate) fn delta_frame(ts_ms: i64, bids: &[(i64, i64)], asks: &[(i64, i64)]) -> Vec<Record> {
        let mut out = Vec::new();
        for &(tick, lots) in bids {
            out.push(depth_rec(LOCAL_BID_DEPTH_EVENT, ts_ms, tick, lots));
        }
        for &(tick, lots) in asks {
            out.push(depth_rec(LOCAL_ASK_DEPTH_EVENT, ts_ms, tick, lots));
        }
        out
    }

    pub(crate) fn write_day(
        root: &std::path::Path,
        symbol: &str,
        day: &str,
        frames: &[Vec<Record>],
    ) {
        let mut w = Writer::create(Vec::new(), test_header(), 1).unwrap();
        for f in frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        let buf = w.into_inner();
        std::fs::write(root.join(format!("{symbol}-{day}.binlog")), &buf).unwrap();
    }

    /// Три уровня: съеден (ровно 70% — граница `eaten`), смешанный, снят.
    /// Лучший бид нигде не догоняет лучший аск: книга не пересекается.
    pub(crate) fn three_level_frames() -> Vec<Vec<Record>> {
        vec![
            snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(105, 10)]),
            vec![
                depth_rec(super::LOCAL_SELL_TRADE_EVENT, 500, 98, 7),
                depth_rec(super::LOCAL_SELL_TRADE_EVENT, 500, 99, 5),
            ],
            delta_frame(1000, &[(96, 10), (98, 1), (99, 1), (100, 1)], &[(105, 10)]),
            delta_frame(2000, &[(96, 1), (98, 1), (99, 1), (100, 1)], &[(105, 10)]),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lob_help_lists_all_subcommands() {
        #[derive(clap::Parser)]
        struct TestCli {
            #[command(subcommand)]
            cmd: LobCommand,
        }
        use clap::CommandFactory;
        let mut top = TestCli::command();
        top.build();
        let mut sorted: Vec<String> = top
            .get_subcommands()
            .map(|s| s.get_name().to_string())
            .filter(|n| n != "help")
            .collect();
        sorted.sort();
        let expected = [
            "clock", "export", "levels", "markout", "pick", "pilot", "power", "probe", "record",
            "session", "verify", "watch",
        ];
        assert_eq!(
            sorted,
            expected.iter().map(ToString::to_string).collect::<Vec<_>>()
        );
    }
}
