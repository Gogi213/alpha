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
//! `LobCommand`, диспетчер `dispatch` и ре-экспорты общего, разложенного по
//! ответственности на четыре файла: реплей суточных файлов в уровни и
//! срезы середины (`replay.rs`: `replay_symbol` и его составляющие — делят
//! `levels`, `markout`, `watch`, `pilot`; сбор `DayTally` `day_tallies` —
//! делят `markout` и `watch`), режим `H3` и общие флаги CLI (`h3.rs`),
//! имена и части файлов записи (`parts.rs`), имена сторон/исходов в CSV
//! (`names.rs`). Всё остальное — в одноимённом с подкомандой файле.
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

use clap::Subcommand;

use crate::bybit::clock::check_rows;

pub mod archive;
pub mod backtest;
pub mod binlog_stats;
pub mod bounce_grid;
pub mod bounce_verdict;
pub mod clock;
pub mod dashboard;
mod export;
pub mod fee_rate;
mod h3;
pub mod latency;
pub mod levels;
pub mod markout;
mod names;
mod parts;
pub mod pick;
pub mod pilot;
pub mod power;
pub mod probe;
pub mod profiles;
pub mod react;
mod record;
mod replay;
pub mod session;
pub mod shortlist;
pub mod touch_profiles;
pub mod touches;
mod verify;
pub mod watch;

pub use archive::{run_archive, ArchiveArgs, ArchiveSummary};
pub use backtest::{run_backtest, BacktestArgs};
pub use binlog_stats::{run_binlog_stats, BinlogStatsArgs};
pub use bounce_grid::{run_bounce_grid, BounceGridArgs, BounceGridSummary};

/// K1 (аудит 18.09): читатели записанных суток — fail-closed по маркеру
/// `verify-<SYMBOL>.status == ok` в корне (В-56): `touches`, `backtest`,
/// `bounce-grid` — как `touch-profiles`. `allow` снимает требование только
/// для отладочных данных; такой прогон в `runs.csv` не идёт.
pub(crate) fn require_verified(
    root: &std::path::Path,
    symbol: &str,
    allow: bool,
) -> anyhow::Result<()> {
    if allow {
        return Ok(());
    }
    let marker = root.join(format!("verify-{symbol}.status"));
    anyhow::ensure!(
        profiles::read_verify_marker(&marker),
        "{}: маркер сверки не `ok` — сутки не проверены (fail-closed, В-56; К1 аудита 18.09);          для отладочных данных — --allow-unverified",
        marker.display()
    );
    Ok(())
}
pub use bounce_verdict::{run_bounce_verdict, BounceVerdictArgs};
pub use clock::{run_clock, ClockArgs};
pub use dashboard::{run_dashboard, DashboardArgs};
pub use fee_rate::{run_fee_rate, FeeRateArgs};
pub use latency::{run_latency, LatencyArgs};
pub use levels::{run_levels, LevelsArgs};
pub use markout::{run_markout, MarkoutArgs};
pub use pick::{run_pick, write_instruments_csv, PickArgs};
pub use pilot::{run_pilot, PilotArgs};
pub use power::{run_power, PowerArgs};
pub use probe::{run_probe, ProbeArgs};
pub use profiles::{run_profiles, ProfilesArgs};
pub use react::{run_react, ReactArgs};
pub use session::{run_session, SessionArgs};
pub use shortlist::{run_shortlist, ShortlistArgs};
pub use touch_profiles::{run_touch_profiles, TouchProfilesArgs};
pub use touches::{run_touches, TouchesArgs};
pub use watch::{run_watch, WatchArgs};

// Общее для нескольких подкоманд разъехалось по файлам (`h3`, `replay`,
// `parts`, `names`); пути `super::…`/`commands::lob::…` у подкоманд и тестов
// не менялись — их держат эти ре-экспорты.
pub(crate) use h3::median_trade_lots_for_symbol;
pub use h3::{
    resolve_h3_mode, resolve_h3_mode_full, resolve_h3_mode_with_k, ExecutionArgs, H3Args,
    H3ModeArg, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS, G0_MIN_PULLED,
};
pub(crate) use names::{death_name, outcome_name, side_name, some_or_empty};
pub(crate) use parts::{
    file_order_key, group_parts_by_day, session_binlog_for, session_days_in_dir, session_parts_for,
    SessionPart,
};
pub(crate) use replay::{
    day_tallies, feed_frames, replay_symbol, replay_symbol_over_configs,
    replay_symbol_touches_and_second_mids, trade_hit_from_record, ReplayDay,
};

// `Record` нужен только тестам: `test_support` ниже и `dashboard/tests.rs`
// (`crate::commands::lob::Record`); боевой `mod.rs` бинлог не читает.
#[cfg(test)]
use crate::binlog::Record;

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
    /// Ставки комиссий аккаунта с биржи против констант кода (подписанный GET, вне горячего пути).
    FeeRate(FeeRateArgs),
    /// Задержки торгового пути на живом счёте: RTT, лимитка/снятие, тейкер — **реальные ордера**.
    Latency(LatencyArgs),
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
    /// Запись всего пула одновременно, один `Feed` на рекордер и (таск 15)
    /// на стратегию: `--minutes` — сессия отладки 5–15 минут (таск 04,
    /// история 7), `--pilot-minutes` — пилот §11 (таск 22), `--always-on` —
    /// олвейс-он коллектор до Ctrl+C, сутки — часть (таск 25, В-34).
    Session(SessionArgs),
    /// Замер реакционного пути и гейт G-LAT, dry-run без ордеров (таск 15,
    /// история 14).
    React(ReactArgs),
    /// Таблица профилей `docs/findings/profiles-<дата>.csv` — первый из трёх
    /// артефактов задачи (таск 10, история 44).
    Profiles(ProfilesArgs),
    /// Вердикт бэктеста с моделью очереди на произвольное число профилей —
    /// второй из трёх артефактов задачи (таск 11, история 32–34).
    Backtest(BacktestArgs),
    /// Прогон-вердикт базовой сетки форм отскока (B5, В-58): покруговые дампы
    /// `--trades-out` → `net_fill`, Шарп и доли причин по форме, `DSR` лучшей
    /// формы по числу испытаний журнала.
    /// Вся сетка форм В-58 (48) по символам и суткам одним процессом: касания
    /// один раз, события посуточно, формы потоками (B6; K1/K2/K4 аудита 18.09).
    BounceGrid(BounceGridArgs),
    BounceVerdict(BounceVerdictArgs),
    /// Шорт-лист на разведочной, заморозка коммитом, подтверждение на
    /// невиденных данных — третий из трёх артефактов задачи (таск 12,
    /// история 24–28, 42).
    Shortlist(ShortlistArgs),
    /// Страница проекта на localhost: что пишет коллектор, что уже насчитано
    /// по накопленному и где мы по гейтам (таск 32, R87). Только чтение
    /// каталога записи; `index.html` и `data.json` — в `--out`.
    Dashboard(DashboardArgs),
    /// Касания живых уровней с признаками практиков — `touches-<SYMBOL>.csv`
    /// (таск 35, В-42): уровень стал лучшей ценой и перестал ею быть,
    /// markout «в сторону отскока», подход, фронтран, круглость, завал.
    Touches(TouchesArgs),
    /// Таблица профилей касаний `docs/findings/touch-profiles-<дата>.csv`
    /// (таск 37, В-44): маргиналы девяти осей касания плюс крест исход ×
    /// возраст, по инструменту и по пулу, `m` «в сторону отскока» с
    /// интервалом по суткам; число испытаний — в `runs.csv`.
    TouchProfiles(TouchProfilesArgs),
    /// Что лежит в бинлоге и за что платятся байты (владелец 2026-09-13:
    /// «оптимизировать коллектор сильно — формат файлов, формат записи, тип
    /// записи, скорость, объём»): записи, кадры, типы событий и живые поля.
    /// Только чтение, ни одного порога.
    BinlogStats(BinlogStatsArgs),
    /// Упаковка закрытых суток в контейнер `*.binlog.zst` (T46): один
    /// zstd-поток над телами кадров даёт −7.7 % к размеру файла на диске
    /// (`docs/findings/archive-compression-2026-09-15.md`), читается тем же
    /// `Reader`, что и обычные сутки. Обязательная сверка round-trip;
    /// оригинал удаляется только явным флагом и только при маркере
    /// `verify-<SYMBOL>.status == ok`.
    Archive(ArchiveArgs),
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
        LobCommand::FeeRate(args) => {
            for line in run_fee_rate(&args)? {
                println!("fee-rate: {line}");
            }
            Ok(())
        }
        LobCommand::Latency(args) => {
            let rep = run_latency(&args)?;
            println!(
                "latency: symbol={} qty={} notional_usdt={} out={}",
                args.symbol,
                crate::bybit::latency::format_e9(rep.qty_e9),
                crate::bybit::latency::format_e9(rep.notional_e9),
                rep.out.display()
            );
            print!("{}", rep.summary);
            for e in &rep.errors {
                println!("latency: error: {e}");
            }
            if let Some(size) = rep.flattened_e9 {
                println!(
                    "latency: в конце закрыт остаток позиции {}",
                    crate::bybit::latency::format_e9(size)
                );
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
        LobCommand::Touches(args) => {
            let summary = run_touches(&args)?;
            println!(
                "touches: days={} touches={} out={}",
                summary.days,
                summary.touches,
                summary.out.display()
            );
            Ok(())
        }
        LobCommand::TouchProfiles(args) => {
            let summary = run_touch_profiles(&args)?;
            println!(
                "touch-profiles: rows={} touches={} trials={} debug={} out={}",
                summary.rows,
                summary.touches,
                summary.trials,
                summary.debug,
                summary.out.display()
            );
            Ok(())
        }
        LobCommand::BinlogStats(args) => {
            let stats = run_binlog_stats(&args)?;
            for line in binlog_stats::summary_lines(&args.path, &stats) {
                println!("{line}");
            }
            if args.reencode {
                for line in binlog_stats::run_reencode(&args)? {
                    println!("{line}");
                }
            }
            if let Some(out) = args.rewrite_out.as_deref() {
                for line in binlog_stats::rewrite_v2_to_v3(&args.path, out)? {
                    println!("{line}");
                }
            }
            Ok(())
        }
        LobCommand::Archive(args) => {
            let summary = run_archive(&args)?;
            for line in archive::summary_lines(&summary) {
                println!("{line}");
            }
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
                "watch: days={} n={} g={} flag={} out={}",
                summary.days,
                summary.n,
                summary.g,
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
        LobCommand::Dashboard(args) => {
            let summary = run_dashboard(&args)?;
            println!(
                "dashboard: coins={} alive={} html={} json={}",
                summary.coins,
                match summary.alive {
                    Some(true) => "yes",
                    Some(false) => "no",
                    None => "unknown",
                },
                summary.html.display(),
                summary.json.display()
            );
            println!("dashboard: {}", summary.alive_reason);
            Ok(())
        }
        LobCommand::React(args) => {
            let report = run_react(&args)?;
            println!("{}", react::format_report(&report));
            Ok(())
        }
        LobCommand::Profiles(args) => {
            let summary = run_profiles(&args)?;
            println!(
                "profiles: rows={} debug={} out={}",
                summary.rows,
                summary.debug,
                summary.out.display()
            );
            Ok(())
        }
        LobCommand::Backtest(args) => {
            let summary = run_backtest(&args)?;
            println!(
                "backtest: profiles={} pass={} red={} out={} pnl_out={}",
                summary.profiles,
                summary.pass,
                summary.red,
                summary.out.display(),
                summary.pnl_out.display()
            );
            Ok(())
        }
        LobCommand::BounceGrid(args) => {
            let s = run_bounce_grid(&args)?;
            println!(
                "bounce-grid: форм {} · символов {} (без маркера {}, без касаний {}) · символ-суток {} · кругов {} · {} · {}",
                s.forms,
                s.symbols_done,
                s.symbols_skipped_unverified,
                s.symbols_without_touches,
                s.symbol_days,
                s.rounds,
                s.rounds_path.display(),
                s.forms_path.display()
            );
            Ok(())
        }
        LobCommand::BounceVerdict(args) => {
            let s = run_bounce_verdict(&args)?;
            let num = |v: Option<f64>| match v {
                Some(x) => format!("{x:.6}"),
                None => "—".to_string(),
            };
            println!(
                "bounce-verdict: ИТОГ={} · форм {} · символов {} · суток {} · испытаний {} (журнал {}) · лучшая {} · кругов {} · суток с кругами {} · net_fill точка={} нижняя={} bps · DSR={} · PBO={} · CPCV={} · {}",
                s.verdict.label(),
                s.forms,
                s.symbols,
                s.days,
                s.trials,
                s.journal_trials,
                s.best_form,
                s.best_n_fills,
                s.best_days,
                num(s.best_point_bps),
                num(s.best_lower_bps),
                num(s.dsr),
                num(s.pbo),
                num(s.cpcv),
                s.out.display()
            );
            Ok(())
        }
        LobCommand::Shortlist(args) => {
            let summary = run_shortlist(&args)?;
            println!(
                "shortlist: trials={} shortlisted={} debug={} verdict={} out={}",
                summary.trials,
                summary.shortlisted,
                summary.debug,
                summary.verdict.as_deref().unwrap_or("n/a (debug)"),
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
        LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_SELL_TRADE_EVENT,
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
            block: false,
            rpi: false,
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

    /// Одна сделка продавца-агрессора (бьёт в бид на `tick`) — то, что
    /// трекер зачтёт как объём против уровня стороны бида.
    pub(crate) fn trade_frame(ts_ms: i64, tick: i64, lots: i64) -> Vec<Record> {
        vec![depth_rec(LOCAL_SELL_TRADE_EVENT, ts_ms, tick, lots)]
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
        write_day_part(root, symbol, day, 1, frames);
    }

    /// Как `write_day`, но на именованную часть суток (таск 22: вторая
    /// сессия тех же суток пишет `-p2`, `-p3`, … вместо затирания первой —
    /// `record::day_file_path` тот же резолвер имени, что и боевой код,
    /// не второй расчёт того же самого).
    pub(crate) fn write_day_part(
        root: &std::path::Path,
        symbol: &str,
        day: &str,
        part: u32,
        frames: &[Vec<Record>],
    ) {
        let mut w = Writer::create(Vec::new(), test_header(), 1).unwrap();
        for f in frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        let buf = w.into_inner();
        let path = crate::commands::record::day_file_path(root, symbol, day, part);
        std::fs::write(path, &buf).unwrap();
    }

    /// Три уровня: съеден (ровно 70% — граница `eaten`), смешанный, снят.
    /// Лучший бид нигде не догоняет лучший аск: книга не пересекается.
    pub(crate) fn three_level_frames() -> Vec<Vec<Record>> {
        vec![
            snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(105, 10)]),
            vec![
                depth_rec(LOCAL_SELL_TRADE_EVENT, 500, 98, 7),
                depth_rec(LOCAL_SELL_TRADE_EVENT, 500, 99, 5),
            ],
            delta_frame(1000, &[(96, 10), (98, 1), (99, 1), (100, 1)], &[(105, 10)]),
            delta_frame(2000, &[(96, 1), (98, 1), (99, 1), (100, 1)], &[(105, 10)]),
        ]
    }
}

#[cfg(test)]
mod tests;
