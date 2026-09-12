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
use crate::bybit::verify::{is_trade_ev, FileReplayer};
use crate::bybit::verify_sidecar::{read_verify_rows, verify_csv_path, VerifyVerdict};
use crate::commands::record::instruments_csv_path;
use crate::lob::levels::{
    DeathKind, H3Mode, LevelObs, LevelRecord, LevelTracker, LevelsConfig, Outcome, TradeHit,
};
use crate::lob::markout::MidSample;
use crate::lob::watch::{tally_day, DayTally};
use hftbacktest::types::LOCAL_BUY_TRADE_EVENT;

pub mod backtest;
pub mod clock;
pub mod dashboard;
mod export;
pub mod levels;
pub mod markout;
pub mod pick;
pub mod pilot;
pub mod power;
pub mod probe;
pub mod profiles;
pub mod react;
mod record;
pub mod session;
pub mod shortlist;
mod verify;
pub mod watch;

pub use backtest::{run_backtest, BacktestArgs};
pub use clock::{run_clock, ClockArgs};
pub use dashboard::{run_dashboard, DashboardArgs};
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
// Режим `H3`: флаг без умолчания (план D-H3) + чтение пола `floor` из
// `instruments.csv`. Общее для `levels`, `markout`, `pilot`, `watch`,
// `profiles`, `shortlist` (переехало из `levels.rs` — таск 17, общий код
// нескольких подкоманд не может лежать в файле одной из них).
// ---------------------------------------------------------------------------

/// Режим порога `H3`, флагом CLI. Явный выбор — умолчания нет: какой режим
/// входит в предрегистрацию, решает двухчасовой пилот, не эта команда.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum H3ModeArg {
    /// Пол в лотах из `instruments.csv`, без прогрева — режим отладки.
    Floor,
    /// 99-й перцентиль по скользящему часу, прогрев 60 мин — прежнее
    /// определение; порог измерен заранее и приходит через `--h3-lots`.
    Percentile,
}

/// Флаги режима `H3` (`--h3-mode`, `--h3-lots`), `#[command(flatten)]` в
/// `levels`/`markout`/`watch`/`profiles`/`shortlist` (таск 17: те же два
/// флага дословно определялись в пяти файлах). `lob pilot` не флаттенит
/// это: он гоняет обе ветки `H3` внутри одного прогона, а не выбирает одну
/// флагом CLI.
#[derive(Debug, Clone, Copy, clap::Args)]
pub struct H3Args {
    /// Режим порога H3: `floor` | `percentile`, без умолчания (план D-H3).
    #[arg(long)]
    pub h3_mode: H3ModeArg,
    /// Порог рождения H3 в лотах: только режим `percentile` — заранее
    /// измеренный 99-й перцентиль. `floor` берёт число из `instruments.csv`
    /// и этот флаг вместе с `floor` — ошибка (`resolve_h3_mode`), не игнор.
    #[arg(long)]
    pub h3_lots: Option<i64>,
}

/// Тройка флагов `BacktestFillModel` (`--median-rtt-ns`, `--p95-rtt-ns`,
/// `--order-qty-e9`) — `#[command(flatten)]` в `profiles`/`shortlist`
/// (таск 17: тройка дословно определялась в обоих файлах). Все три
/// опциональны и включают модель исполнения только вместе
/// (`resolve_fill_model`); не заданы — `NoFillModel`, как раньше.
///
/// `lob backtest` **не** флаттенит эту структуру: там та же тройка —
/// обязательные флаги без умолчания (нет `NoFillModel`, бэктест не бывает
/// без RTT/лота), а `Option<i64>` здесь убрал бы `--median-rtt-ns` и соседей
/// из строки `Usage` как обязательные — заметная перемена `--help`, которую
/// критерий приёмки таска 17 запрещает. Общая структура для настоящего
/// разного контракта CLI (обязательно/опционально) была бы либо тем же
/// изменением поведения, либо второй структурой с другим именем — то есть
/// не тем упрощением, которого просит пункт 2.
#[derive(Debug, Clone, Copy, clap::Args)]
pub struct ExecutionArgs {
    /// Медианная замеренная RTT исполнения, нс — параметр `BacktestFillModel`
    /// (таск 16), тот же смысл и источник, что `lob backtest
    /// --median-rtt-ns` (D-RTT: `lob probe`/`clock.csv`). Обязана быть задана
    /// вместе с `--p95-rtt-ns`/`--order-qty-e9` — все три сразу включают
    /// модель исполнения поверх `lob::backtest` (`resolve_fill_model`); без
    /// всех трёх (умолчание — не задан ни один) команда остаётся на
    /// `NoFillModel`, как раньше. Задавать только часть тройки — ошибка:
    /// изобретать недостающее число запрещено (§9).
    #[arg(long)]
    pub median_rtt_ns: Option<i64>,
    /// 95-й перцентиль той же замеренной RTT — см. `median_rtt_ns`.
    #[arg(long)]
    pub p95_rtt_ns: Option<i64>,
    /// Размер круга в 1e-9 лотов — минимальный лот площадки (Decision 22,
    /// тот же смысл, что `lob backtest --order-qty-e9`) — см. `median_rtt_ns`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
}

/// Строка `instruments.csv`, нужная режиму `floor`: символ и колонка
/// `h3_lots` (пишет отдельный шаг сборки пула, план D-H3). `h3_lots` читается
/// строкой — у большинства символов колонка сейчас пуста, парсинг в целое
/// откладывается до найденной строки нужного символа.
#[derive(Debug, serde::Deserialize)]
struct H3FloorRow {
    symbol: String,
    h3_lots: String,
}

/// Пол `H3` символа из `instruments.csv`: нет файла, нет колонки, нет
/// символа или значение не положительное — понятная ошибка с ненулевым
/// кодом выхода, а не молчаливый ноль (критерий приёмки таска 02).
fn h3_lots_for_symbol(instruments_csv: &Path, symbol: &str) -> anyhow::Result<i64> {
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок вместо настоящего.
    let mut r = pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — режиму floor нужен instruments.csv с колонкой h3_lots \
             (пишет отдельный шаг сборки пула)",
            instruments_csv.display()
        )
    })?;
    let headers = r.headers()?.clone();
    anyhow::ensure!(
        headers.iter().any(|h| h == "h3_lots"),
        "{}: нет колонки h3_lots (пишет отдельный шаг сборки пула)",
        instruments_csv.display()
    );
    for row in r.deserialize::<H3FloorRow>() {
        let row = row?;
        if row.symbol != symbol {
            continue;
        }
        let raw = row.h3_lots.trim();
        let v: i64 = raw
            .parse()
            .map_err(|_| anyhow::anyhow!("{symbol}: h3_lots {raw:?} в instruments.csv не целое"))?;
        anyhow::ensure!(
            v > 0,
            "{symbol}: h3_lots обязан быть положителен, получено {v}"
        );
        return Ok(v);
    }
    anyhow::bail!(
        "{symbol}: нет строки в {} (режим floor)",
        instruments_csv.display()
    );
}

/// Строка `instruments.csv`, нужная относительному порогу `--h3-k` (таск 18,
/// В-30/D05): символ и колонка `median_trade_lots` — пишет `lob pick`
/// (таск 08), та же колонка, что несёт `h3_lots`/`k` рядом.
#[derive(Debug, serde::Deserialize)]
struct MedianTradeLotsRow {
    symbol: String,
    median_trade_lots: String,
}

/// Медиана размера сделки символа из `instruments.csv` — вход
/// `pick::h3_lots_floor` для `--h3-k`/сетки `pilot::K_GRID` (таск 18,
/// В-30/D05). Нет файла, нет колонки, нет строки символа или значение не
/// положительное — понятная ошибка (тот же приём, что `h3_lots_for_symbol`),
/// не молчаливый ноль.
pub(crate) fn median_trade_lots_for_symbol(
    instruments_csv: &Path,
    symbol: &str,
) -> anyhow::Result<i64> {
    let mut r = pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — --h3-k нужен instruments.csv с колонкой median_trade_lots (пишет lob pick)",
            instruments_csv.display()
        )
    })?;
    let headers = r.headers()?.clone();
    anyhow::ensure!(
        headers.iter().any(|h| h == "median_trade_lots"),
        "{}: нет колонки median_trade_lots (пишет lob pick)",
        instruments_csv.display()
    );
    for row in r.deserialize::<MedianTradeLotsRow>() {
        let row = row?;
        if row.symbol != symbol {
            continue;
        }
        let raw = row.median_trade_lots.trim();
        anyhow::ensure!(
            !raw.is_empty(),
            "{symbol}: median_trade_lots не измерена в {} (окно lob pick не поймало сделок)",
            instruments_csv.display()
        );
        let v: i64 = raw.parse().map_err(|_| {
            anyhow::anyhow!("{symbol}: median_trade_lots {raw:?} в instruments.csv не целое")
        })?;
        anyhow::ensure!(
            v > 0,
            "{symbol}: median_trade_lots обязана быть положительна, получено {v}"
        );
        return Ok(v);
    }
    anyhow::bail!(
        "{symbol}: нет строки в {} (median_trade_lots)",
        instruments_csv.display()
    );
}

/// Режим `H3` из флага: `floor` читает пол из `instruments.csv` корня
/// записи, `percentile` берёт заранее измеренный порог из `--h3-lots`
/// (обязателен в этом режиме — измерение вне этой команды). `--h3-lots`
/// вместе с `floor` — громкая ошибка (таск 17, критерий приёмки), а не
/// молчаливый игнор значения, которое `floor` не читает.
pub fn resolve_h3_mode(
    root: &Path,
    symbol: &str,
    mode: H3ModeArg,
    h3_lots: Option<i64>,
) -> anyhow::Result<H3Mode> {
    resolve_h3_mode_with_k(root, symbol, mode, h3_lots, None)
}

/// Как `resolve_h3_mode`, плюс относительный порог `--h3-k` (таск 18,
/// В-30/D05): `k` действует только вместе с `floor` — порог `h3_lots =
/// floor(k × median_trade_lots)` (`pick::h3_lots_floor`), медиана — из той
/// же строки `instruments.csv`, что несёт колонку `h3_lots`. Без `--h3-k` —
/// прежнее поведение (`resolve_h3_mode` делегирует сюда с `h3_k = None`,
/// колонка `h3_lots` целиком). `--h3-k` вместе с `percentile` — громкая
/// ошибка: `k` параметризует только относительный порог `floor`, у
/// `percentile` свой заранее измеренный перцентиль (критерий приёмки
/// таска 18). Единственный вызывающий сегодня — `lob levels`; сетка
/// `pilot::K_GRID` зовёт `pick::h3_lots_floor`/`median_trade_lots_for_symbol`
/// напрямую, минуя эту обёртку (ей нужно пять порогов за один реплей, не
/// один).
pub fn resolve_h3_mode_with_k(
    root: &Path,
    symbol: &str,
    mode: H3ModeArg,
    h3_lots: Option<i64>,
    h3_k: Option<f64>,
) -> anyhow::Result<H3Mode> {
    match mode {
        H3ModeArg::Floor => {
            anyhow::ensure!(
                h3_lots.is_none(),
                "--h3-lots несовместим с --h3-mode floor: порог берётся из instruments.csv"
            );
            let instruments_csv = instruments_csv_path(root);
            let h3_lots = match h3_k {
                Some(k) => {
                    let median = median_trade_lots_for_symbol(&instruments_csv, symbol)?;
                    pick::h3_lots_floor(Some(median), k).ok_or_else(|| {
                        anyhow::anyhow!("{symbol}: h3_lots_floor(k={k}) не посчитался")
                    })?
                }
                None => h3_lots_for_symbol(&instruments_csv, symbol)?,
            };
            anyhow::ensure!(
                h3_lots > 0,
                "{symbol}: h3_lots (k={h3_k:?}) обязан быть положителен, получено {h3_lots}"
            );
            Ok(H3Mode::Floor { h3_lots })
        }
        H3ModeArg::Percentile => {
            anyhow::ensure!(
                h3_k.is_none(),
                "--h3-k несовместим с --h3-mode percentile: k параметризует только floor (В-30/D05)"
            );
            let h3_lots = h3_lots.ok_or_else(|| {
                anyhow::anyhow!(
                    "--h3-lots обязателен в режиме percentile: порог измеряется заранее"
                )
            })?;
            Ok(H3Mode::Percentile { h3_lots })
        }
    }
}

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

/// Рабочее состояние одних суток при нескольких конфигурациях `H3` разом
/// (таск 18, В-30/D05: сетка `pilot::K_GRID` — пять порогов за один
/// декодированный проход, не пять реплеев): книга и конвертер пересоздаются
/// на каждый файл (каждый начинается со снапшота), трекеры живут все части
/// суток — окно `repeat_count` и прогрев считаются по суткам. `mids` один на
/// сутки — срез середины не зависит от конфигурации `H3`, только `records`
/// и трекеры множатся по числу конфигураций. `replay_symbol` — частный
/// случай с одной конфигурацией, реализован через `replay_symbol_over_configs`.
struct DayWork {
    day: String,
    records: Vec<Vec<LevelRecord>>,
    mids: Vec<MidSample>,
    trackers: Vec<LevelTracker>,
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
/// срезом середины. Вызывается только после успешного `apply`. Одна
/// конфигурация — частный случай `feed_frames_multi` (таск 18), но
/// оставлена отдельной функцией: `profiles.rs::run_profiles_with_fill_model`
/// (вне зоны таска 18, «не трогать») зовёт её напрямую с одним трекером,
/// вторая сигнатура (`&mut [LevelTracker]`) поменяла бы её вызов.
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

/// Применяет обновление к книге и кормит **все** трекеры кадром обеих
/// сторон плюс общим срезом середины (таск 18: `LevelObs` не зависит от
/// порога `H3`, поэтому строится один раз на кадр и раздаётся всем
/// трекерам — не по разу на конфигурацию). Вызывается только после
/// успешного `apply`.
fn feed_frames_multi(
    book: &Book,
    trackers: &mut [LevelTracker],
    ts_ms: i64,
    out: &mut [Vec<LevelRecord>],
    mids: &mut Vec<MidSample>,
) {
    debug_assert_eq!(
        trackers.len(),
        out.len(),
        "трекер и выход обязаны идти парой"
    );
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
        for (tracker, out) in trackers.iter_mut().zip(out.iter_mut()) {
            tracker.observe_frame(ts_ms, side, &obs, out);
        }
    }
    if let (Some(bid), Some(ask)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
        mids.push(MidSample {
            ts_ms,
            bid_tick: bid,
            ask_tick: ask,
        });
    }
}

/// Реплей всех суточных файлов символа сразу через несколько конфигураций
/// `H3` (таск 18, критерий приёмки «за один реплей на инструмент, не
/// пять»): декодирование бинлога, применение к книге — один проход по
/// файлам и кадрам, тем же кодом, что файловый `verify` (`Reader` читает
/// кадры, `FileReplayer` группирует их в обновления, `Book` их применяет);
/// `LevelTracker` на каждую конфигурацию развешивает уровни и трейды
/// независимо, кормится одними и теми же кадрами (`feed_frames_multi`).
/// Разрыв последовательности (`Err` из `apply`) останавливает файл для всех
/// конфигураций разом, как в `verify`: дальше этот файл недоверен,
/// следующий идёт с чистого листа. Возвращает `ReplayStats` в том же
/// порядке, что `cfgs`.
/// Все `<SYMBOL>-*.binlog` каталога, хронологически (день, потом часть) —
/// общий шаг `replay_symbol_over_configs` (запись `lob record`, много суток)
/// и `session_binlog_for` (запись `lob session`, с таска 22 — тоже может
/// нести несколько суток и несколько частей на сутки: владелец пишет одну-две
/// сессии в сутки в тот же `--root`, не одну на весь каталог). Пустой список
/// не ошибка здесь — у обоих вызывающих свой текст на пустоту (общий на
/// «нет файлов вовсе», разный на подсказку про старый недатированный формат).
fn list_symbol_binlogs(dir: &Path, symbol: &str) -> anyhow::Result<Vec<PathBuf>> {
    let prefix = format!("{symbol}-");
    let entries = std::fs::read_dir(dir)
        .map_err(|e| anyhow::anyhow!("каталог {} не читается: {e}", dir.display()))?;
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
    Ok(files)
}

fn replay_symbol_over_configs(
    root: &Path,
    symbol: &str,
    cfgs: &[LevelsConfig],
) -> anyhow::Result<Vec<ReplayStats>> {
    anyhow::ensure!(
        !cfgs.is_empty(),
        "replay_symbol_over_configs: пустая сетка конфигураций"
    );
    let prefix = format!("{symbol}-");
    let files = list_symbol_binlogs(root, symbol)?;
    if files.is_empty() {
        // Таск 19, критерий 4: каталог старого формата (до таска 19 `lob
        // session` писала `<SYMBOL>.binlog` без даты) не должен молча
        // выглядеть как «нет суточных файлов» — владелец переименовывает
        // руками, но узнать об этом обязан из сообщения, не из тишины.
        let undated = root.join(format!("{symbol}.binlog"));
        if undated.is_file() {
            anyhow::bail!(
                "файл `{symbol}.binlog` без даты — запись старого формата, переименуйте в \
                 `{symbol}-<дата>.binlog` ({} в {})",
                undated.display(),
                root.display()
            );
        }
        anyhow::bail!("нет суточных файлов {prefix}*.binlog в {}", root.display());
    }
    // Счётчики GC (байт/записей) — по одному экземпляру на конфигурацию,
    // хотя декодирование общее: значения совпадут у всех, но `+=` читает
    // поле, а не только пишет его (та же идиома, что у `replay_symbol` до
    // этого таска — единственная актуальная альтернатива читать поле
    // разом после цикла тем же способом, каким это уже делает `ReplayStats
    // .days` ниже).
    let mut out: Vec<ReplayStats> = cfgs
        .iter()
        .map(|_| ReplayStats {
            days: Vec::new(),
            bytes: 0,
            records: 0,
        })
        .collect();
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
        for s in &mut out {
            s.bytes += data.len() as u64;
        }
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let header = reader.header();
        if work.last().is_none_or(|w| w.day != day) {
            work.push(DayWork {
                day,
                records: cfgs.iter().map(|_| Vec::new()).collect(),
                mids: Vec::new(),
                trackers: cfgs.iter().map(|&cfg| LevelTracker::new(cfg)).collect(),
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
            for s in &mut out {
                s.records += frame_records.len() as u64;
            }
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
                    feed_frames_multi(
                        &book,
                        &mut entry.trackers,
                        up.cts_ms,
                        &mut entry.records,
                        &mut entry.mids,
                    );
                }
                if !file_ok {
                    break;
                }
                if let Some(h) = hit {
                    for tracker in &mut entry.trackers {
                        tracker.observe_trade(h);
                    }
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
                feed_frames_multi(
                    &book,
                    &mut entry.trackers,
                    up.cts_ms,
                    &mut entry.records,
                    &mut entry.mids,
                );
            }
        }
    }
    // Раскладка по конфигурации (таск 18): `w.records[i]` — уровни i-й
    // конфигурации за эти сутки, `w.mids` общий и клонируется в каждую
    // раскладку (срез середины не зависит от `H3`, дороже перечитать бинлог
    // ради него ещё раз, чем скопировать уже посчитанный вектор).
    for w in work {
        for (i, records) in w.records.into_iter().enumerate() {
            out[i].days.push(ReplayDay {
                day: w.day.clone(),
                records,
                mids: w.mids.clone(),
            });
        }
    }
    Ok(out)
}

/// Реплей всех суточных файлов символа одной конфигурацией `H3` — частный
/// случай `replay_symbol_over_configs` с сеткой из одного элемента
/// (таск 18: код декодирования один, конфигураций может быть несколько).
fn replay_symbol(root: &Path, symbol: &str, cfg: LevelsConfig) -> anyhow::Result<ReplayStats> {
    let mut out = replay_symbol_over_configs(root, symbol, std::slice::from_ref(&cfg))?;
    Ok(out
        .pop()
        .expect("replay_symbol_over_configs с одним cfg обязан вернуть один ReplayStats"))
}

/// Резолвер бинлогов сессии (таск 19, находка G4; список частей — таск 22).
/// До таска 22 каталог `lob session` нёс не больше одного файла на символ
/// (ровно один прогон). С этого таска `lob session --root` можно вызывать
/// несколько раз в те же сутки (В-32, «одна-две сессии в сутки») и часть
/// суток больше не единственная: `session_binlog_path`/`claim_part` отдают
/// следующий свободный номер вместо `part = 1` жёстко, ничего не затирая, а
/// эта функция возвращает **все** файлы символа в каталоге по порядку записи
/// — день, потом часть внутри дня (`list_symbol_binlogs`, тот же ключ
/// `file_order_key`, что уже сортирует много-частевую запись `lob record`
/// выше) — читатели проигрывают их подряд как один поток.
///
/// - Один и больше датированных файлов — это и есть бинлоги сессии, в
///   хронологическом порядке.
/// - Ни одного, но есть файл старого формата `<symbol>.binlog` без даты —
///   явная ошибка с именем файла и советом переименовать (не тихое «нет
///   файлов», см. `replay_symbol_over_configs`/`bybit::verify::run_verify`
///   выше — тот же приём).
/// - Ни одного и старого формата тоже нет — общая ошибка «нет бинлога».
pub(crate) fn session_binlog_for(dir: &Path, symbol: &str) -> anyhow::Result<Vec<PathBuf>> {
    let files = list_symbol_binlogs(dir, symbol)?;
    if files.is_empty() {
        let undated = dir.join(format!("{symbol}.binlog"));
        if undated.is_file() {
            anyhow::bail!(
                "файл `{symbol}.binlog` без даты — запись старого формата, переименуйте в \
                 `{symbol}-<дата>.binlog` ({} в {})",
                undated.display(),
                dir.display()
            );
        }
        anyhow::bail!("нет бинлога для `{symbol}` в {}", dir.display());
    }
    Ok(files)
}

/// Одна часть записи сессии с её **собственными** сутками и часом старта
/// (таск 23). До него `profiles`/`watch` брали `day_utc` и `start_hour_utc`
/// из верхнего `session.json` каталога — а его перезаписывает последняя
/// сессия в этот `--root`, и каталог с частями за D и D+1 отдавал всё как
/// D+1: сутки переставали быть кластером, окно «сейчас» фильтровало по
/// завышенному дню. Здесь сутки — из имени файла части
/// (`<SYMBOL>-<день>[-pN].binlog`, то же имя выбирает `record::claim_part`
/// по моменту старта сессии), час — из `session.json.binlog_files` своей
/// части (запись с теми же символом, номером и сутками `started_utc`,
/// таск 22), а если часть там не перечислена (запись до таска 22) — из
/// `start_hour_utc` каталога, как раньше.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SessionPart {
    pub path: PathBuf,
    pub part: u32,
    /// Календарные сутки старта части, UTC, `YYYY-MM-DD`.
    pub day_utc: String,
    pub start_hour_utc: u32,
}

/// Подмножество `session.json`, нужное атрибуции частей: час старта
/// каталога (запасной путь) и перечень частей с их `started_utc`.
/// Старый формат без `binlog_files` — пустой перечень, не ошибка.
#[derive(Debug, serde::Deserialize)]
struct SessionPartsPeek {
    start_hour_utc: u32,
    #[serde(default)]
    binlog_files: Vec<session::BinlogPart>,
}

fn hour_of_started_utc(started_utc: &str) -> Option<u32> {
    started_utc.get(11..13)?.parse().ok()
}

/// Части сессии символа в каталоге — те же файлы и в том же порядке, что
/// `session_binlog_for`, но с сутками и часом старта на каждую часть.
/// Каталог без разборного `session.json` — ошибка (не сессия), как и
/// отсутствие файлов символа; читатели многих каталогов подряд пропускают
/// такой каталог молча — тот же приём, что у `session_binlog_for`.
pub(crate) fn session_parts_for(dir: &Path, symbol: &str) -> anyhow::Result<Vec<SessionPart>> {
    let meta_path = dir.join("session.json");
    let text = std::fs::read_to_string(&meta_path)
        .map_err(|e| anyhow::anyhow!("{} не читается: {e}", meta_path.display()))?;
    let meta: SessionPartsPeek = serde_json::from_str(&text)
        .map_err(|e| anyhow::anyhow!("{} не разбирается: {e}", meta_path.display()))?;
    let prefix = format!("{symbol}-");
    session_binlog_for(dir, symbol)?
        .into_iter()
        .map(|path| {
            let name = path
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let (day_utc, part) = file_order_key(&prefix, &name);
            let start_hour_utc = meta
                .binlog_files
                .iter()
                .find(|b| {
                    b.symbol == symbol && b.part == part && b.started_utc.starts_with(&day_utc)
                })
                .and_then(|b| hour_of_started_utc(&b.started_utc))
                .unwrap_or(meta.start_hour_utc);
            Ok(SessionPart {
                path,
                part,
                day_utc,
                start_hour_utc,
            })
        })
        .collect()
}

/// Части, сгруппированные по суткам в хронологическом порядке — единица
/// реплея для `profiles`/`watch` (таск 23): трекер уровней общий на части
/// одних суток (таск 22, «части читаются подряд как один поток») и чистый
/// на каждые новые сутки — между сутками разрыв записи есть всегда.
pub(crate) fn group_parts_by_day(parts: Vec<SessionPart>) -> Vec<(String, Vec<SessionPart>)> {
    let mut out: Vec<(String, Vec<SessionPart>)> = Vec::new();
    for part in parts {
        match out.last_mut() {
            Some((day, group)) if *day == part.day_utc => group.push(part),
            _ => out.push((part.day_utc.clone(), vec![part])),
        }
    }
    out
}

/// Сутки, представленные в каталоге сессии хотя бы одной датированной
/// частью любого символа — по возрастанию. Каталог без `session.json` — не
/// сессия, пустое множество; недатированный `<SYMBOL>.binlog` старого
/// формата суток не даёт. Это вход окна «сейчас» (`profiles`: перечень
/// суток для предрегистрации и счёт сессий внутри/снаружи) и календаря
/// `shortlist` — оба до таска 23 читали одни сутки `started_utc` каталога.
pub(crate) fn session_days_in_dir(dir: &Path) -> std::collections::BTreeSet<String> {
    let mut days = std::collections::BTreeSet::new();
    if !dir.join("session.json").is_file() {
        return days;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return days;
    };
    for e in entries.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if let Some(day) = day_of_binlog_name(&name) {
            days.insert(day.to_string());
        }
    }
    days
}

/// Сутки из имени `<SYMBOL>-<день>[-pN].binlog` без знания символа: хвост
/// после необязательного `-pN` обязан кончаться на `-YYYY-MM-DD`. Иначе —
/// `None` (старый недатированный формат, чужой файл).
fn day_of_binlog_name(name: &str) -> Option<&str> {
    let stem = name.strip_suffix(".binlog")?;
    let stem = match stem.rsplit_once("-p") {
        Some((head, num)) if num.parse::<u32>().is_ok() => head,
        _ => stem,
    };
    let split = stem.len().checked_sub(11)?;
    let head = stem.get(..split)?;
    let day = stem.get(split..)?.strip_prefix('-')?;
    (!head.is_empty() && crate::lob::watch::day_format_ok(day)).then_some(day)
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

/// Собирает `DayTally` из реплея тем же `tally_day`, что читает G1.
/// Качество суток — из `verify.csv` шага 0.8 (расхождения теста 1) в корне
/// записи. Отсутствующий файл — ноль строк, а не ошибка: сутки без проверок
/// негодны по правилу `day_eligible` (ноль проверок — не годно), и это
/// честный красный, а не падение команды.
///
/// Знаменатель доли — строки с решённым сравнением (`Ok`/`Mismatch`),
/// числитель — строки `Mismatch`; `Misaligned`/`RestUnavailable` —
/// неопределённые, как `trades_indeterminate` в `verify`.
fn day_tallies(root: &Path, symbol: &str, days: &[ReplayDay]) -> anyhow::Result<Vec<DayTally>> {
    let verify_rows =
        read_verify_rows(&verify_csv_path(root)).map_err(|e| anyhow::anyhow!("verify.csv: {e}"))?;
    days.iter()
        .map(|day| {
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
            Ok(tally_day(symbol, &day.day, violations, basis))
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
    /// Шорт-лист на разведочной, заморозка коммитом, подтверждение на
    /// невиденных данных — третий из трёх артефактов задачи (таск 12,
    /// история 24–28, 42).
    Shortlist(ShortlistArgs),
    /// Страница проекта на localhost: что пишет коллектор, что уже насчитано
    /// по накопленному и где мы по гейтам (таск 32, R87). Только чтение
    /// каталога записи; `index.html` и `data.json` — в `--out`.
    Dashboard(DashboardArgs),
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
                "dashboard: instruments={} alive={} html={} json={}",
                summary.instruments,
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
mod tests {
    use super::*;

    /// Критерий приёмки таска 17: `--h3-lots` вместе с `--h3-mode floor` —
    /// громкая ошибка (`resolve_h3_mode` — общий шов пяти подкоманд), не
    /// молчаливый игнор значения, которое `floor` не читает.
    #[test]
    fn resolve_h3_mode_rejects_h3_lots_with_floor() {
        let dir = tempfile::tempdir().unwrap();
        let err = resolve_h3_mode(dir.path(), "SOLUSDT", H3ModeArg::Floor, Some(5)).unwrap_err();
        assert!(
            err.to_string().contains("h3-lots") || err.to_string().contains("floor"),
            "сообщение обязано назвать конфликт --h3-lots/--h3-mode floor: {err}"
        );
    }

    /// Критерий приёмки таска 18 (В-30/D05): `--h3-k` вместе с
    /// `--h3-mode percentile` — громкая ошибка, `k` параметризует только
    /// относительный порог `floor`.
    #[test]
    fn resolve_h3_mode_with_k_rejects_h3_k_with_percentile() {
        let dir = tempfile::tempdir().unwrap();
        let err = resolve_h3_mode_with_k(
            dir.path(),
            "SOLUSDT",
            H3ModeArg::Percentile,
            Some(5),
            Some(2.0),
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("h3-k") || err.to_string().contains("percentile"),
            "сообщение обязано назвать конфликт --h3-k/--h3-mode percentile: {err}"
        );
    }

    /// `--h3-k` с `floor` считает `h3_lots = floor(k × median_trade_lots)`
    /// из колонки `instruments.csv`, не колонку `h3_lots` (та пустая в этой
    /// фикстуре — читатель обязан её не тронуть).
    #[test]
    fn resolve_h3_mode_with_k_computes_floor_from_median_trade_lots() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            instruments_csv_path(dir.path()),
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots\n\
             SOLUSDT,0.01,0.1,0.1,5,,,7\n",
        )
        .unwrap();
        let mode = resolve_h3_mode_with_k(dir.path(), "SOLUSDT", H3ModeArg::Floor, None, Some(1.5))
            .unwrap();
        assert_eq!(mode, H3Mode::Floor { h3_lots: 10 }, "floor(1.5*7) = 10");
    }

    #[test]
    fn median_trade_lots_for_symbol_reads_the_column() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            instruments_csv_path(dir.path()),
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots\n\
             SOLUSDT,0.01,0.1,0.1,5,10,2.0,7\n",
        )
        .unwrap();
        assert_eq!(
            median_trade_lots_for_symbol(&instruments_csv_path(dir.path()), "SOLUSDT").unwrap(),
            7
        );
    }

    #[test]
    fn median_trade_lots_for_symbol_errors_without_the_column() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            instruments_csv_path(dir.path()),
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value\nSOLUSDT,0.01,0.1,0.1,5\n",
        )
        .unwrap();
        let err =
            median_trade_lots_for_symbol(&instruments_csv_path(dir.path()), "SOLUSDT").unwrap_err();
        assert!(
            err.to_string().contains("median_trade_lots"),
            "сообщение обязано назвать недостающую колонку: {err}"
        );
    }

    /// Критерий приёмки таска 18: сетка `k` пилота кормится одним
    /// декодированием бинлога, не пятью — `replay_symbol_over_configs` на
    /// нескольких порогах разом обязан дать те же числа, что отдельные
    /// вызовы `replay_symbol` на каждом пороге по отдельности, и разный
    /// порог обязан дать разный (не больший при большем пороге) счёт
    /// уровней на этой фикстуре.
    #[test]
    fn replay_symbol_over_configs_matches_single_config_replay_per_threshold() {
        let dir = tempfile::tempdir().unwrap();
        test_support::write_day(
            dir.path(),
            "SOLUSDT",
            "2026-09-08",
            &test_support::three_level_frames(),
        );
        let cfg_low = LevelsConfig {
            mode: H3Mode::Floor { h3_lots: 1 },
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
        };
        let cfg_high = LevelsConfig {
            mode: H3Mode::Floor { h3_lots: 9 },
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
        };
        let multi =
            replay_symbol_over_configs(dir.path(), "SOLUSDT", &[cfg_low, cfg_high]).unwrap();
        let single_low = replay_symbol(dir.path(), "SOLUSDT", cfg_low).unwrap();
        let single_high = replay_symbol(dir.path(), "SOLUSDT", cfg_high).unwrap();
        assert_eq!(multi.len(), 2);
        assert_eq!(
            multi[0].days[0].records.len(),
            single_low.days[0].records.len()
        );
        assert_eq!(
            multi[1].days[0].records.len(),
            single_high.days[0].records.len()
        );
        assert!(
            multi[1].days[0].records.len() <= multi[0].days[0].records.len(),
            "выше порог — не больше рождений: {} vs {}",
            multi[1].days[0].records.len(),
            multi[0].days[0].records.len()
        );
    }

    /// Таск 19, критерий 4: каталог старого формата (до таска 19 `lob
    /// session` писала `<SYMBOL>.binlog` без даты) обязан провалиться с
    /// явным сообщением про переименование, не с общим «нет суточных
    /// файлов» — иначе владелец ищет разгадку не там.
    #[test]
    fn undated_symbol_binlog_fails_with_an_explicit_rename_message_not_silent_no_files() {
        let dir = tempfile::tempdir().unwrap();
        // Старая раскладка `lob session` (до таска 19): файл без даты.
        std::fs::write(dir.path().join("SOLUSDT.binlog"), b"stub").unwrap();
        let cfg = LevelsConfig {
            mode: H3Mode::Floor { h3_lots: 1 },
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
        };
        let err = match replay_symbol(dir.path(), "SOLUSDT", cfg) {
            Ok(_) => panic!("файл без даты обязан провалить реплей"),
            Err(e) => e,
        };
        let msg = err.to_string();
        assert!(
            msg.contains("SOLUSDT.binlog") && msg.contains("переименуйте"),
            "сообщение обязано назвать файл и посоветовать переименование: {msg}"
        );
    }

    /// Тот же каталог без вообще никакого файла символа — сообщение
    /// остаётся прежним, общим «нет суточных файлов» (регресс-тест против
    /// того, чтобы находка старого формата подменила собой пустой каталог).
    #[test]
    fn missing_symbol_files_still_get_the_generic_no_daily_files_message() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = LevelsConfig {
            mode: H3Mode::Floor { h3_lots: 1 },
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
        };
        let err = match replay_symbol(dir.path(), "SOLUSDT", cfg) {
            Ok(_) => panic!("пустой каталог обязан провалить реплей"),
            Err(e) => e,
        };
        let msg = err.to_string();
        assert!(
            msg.contains("нет суточных файлов") && !msg.contains("переименуйте"),
            "без файла вовсе сообщение обязано остаться общим: {msg}"
        );
    }

    // -----------------------------------------------------------------
    // `session_binlog_for` — резолвер одной сессии (таск 19, часть 2):
    // `profiles.rs`/`watch.rs`/`backtest.rs`/`bybit::verify` замыкаются на
    // него вместо литерала `<SYMBOL>.binlog` или своей копии поиска.
    // -----------------------------------------------------------------

    #[test]
    fn session_binlog_for_finds_the_single_dated_file() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
        let paths = session_binlog_for(dir.path(), "SOLUSDT").unwrap();
        assert_eq!(paths, vec![dir.path().join("SOLUSDT-2026-09-08.binlog")]);
    }

    #[test]
    fn session_binlog_for_reports_the_undated_legacy_file_by_name_with_a_rename_hint() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("SOLUSDT.binlog"), b"stub").unwrap();
        let err = session_binlog_for(dir.path(), "SOLUSDT").unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("SOLUSDT.binlog") && msg.contains("переименуйте"),
            "сообщение обязано назвать файл и посоветовать переименование: {msg}"
        );
    }

    #[test]
    fn session_binlog_for_reports_nothing_found_when_the_symbol_never_appears() {
        let dir = tempfile::tempdir().unwrap();
        let err = session_binlog_for(dir.path(), "SOLUSDT").unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("SOLUSDT") && !msg.contains("переименуйте"),
            "без файла вовсе сообщение не обязано упоминать переименование: {msg}"
        );
    }

    /// Таск 22, критерий 3: несколько сессий в те же сутки (`-p2`, `-p3`, …)
    /// — не ошибка «путаница каталогов» (старое поведение таска 19), а список
    /// частей в порядке записи. `-p2` голой лексикографией сортировался бы
    /// раньше файла без суффикса (`-` < `.` в ASCII) — тест ловит именно
    /// инверсию, а не только «оба файла присутствуют».
    #[test]
    fn session_binlog_for_orders_same_day_parts_by_part_number_not_lexicographically() {
        let dir = tempfile::tempdir().unwrap();
        let p1 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
        let p2 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 2);
        let p3 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 3);
        // Порядок записи на диск — намеренно не по возрастанию имени, чтобы
        // тест не мог случайно совпасть с порядком `std::fs::read_dir`.
        std::fs::write(&p2, b"stub").unwrap();
        std::fs::write(&p3, b"stub").unwrap();
        std::fs::write(&p1, b"stub").unwrap();
        let paths = session_binlog_for(dir.path(), "SOLUSDT").unwrap();
        assert_eq!(
            paths,
            vec![p1, p2, p3],
            "части одних суток — по возрастанию номера"
        );
    }

    /// Таск 22: каталог теперь может нести несколько суток (владелец гоняет
    /// `lob session` в тот же `--root` день за днём) — сортировка сперва по
    /// дате, потом по части внутри даты.
    #[test]
    fn session_binlog_for_orders_multiple_days_by_date_then_part() {
        let dir = tempfile::tempdir().unwrap();
        let day1_p1 =
            crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
        let day1_p2 =
            crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 2);
        let day2_p1 =
            crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-09", 1);
        std::fs::write(&day2_p1, b"stub").unwrap();
        std::fs::write(&day1_p2, b"stub").unwrap();
        std::fs::write(&day1_p1, b"stub").unwrap();
        let paths = session_binlog_for(dir.path(), "SOLUSDT").unwrap();
        assert_eq!(paths, vec![day1_p1, day1_p2, day2_p1]);
    }

    // -----------------------------------------------------------------
    // `session_parts_for` / `group_parts_by_day` / `session_days_in_dir` —
    // сутки и час старта на уровне части, не каталога (таск 23).
    // -----------------------------------------------------------------

    fn write_session_json(dir: &Path, start_hour_utc: u32, parts: &[(&str, u32, &str)]) {
        let files: Vec<String> = parts
            .iter()
            .map(|(symbol, part, started)| {
                format!("{{\"symbol\":\"{symbol}\",\"part\":{part},\"started_utc\":\"{started}\"}}")
            })
            .collect();
        let json = format!(
            "{{\"started_utc\":\"2026-09-09T14:00:00Z\",\"start_hour_utc\":{start_hour_utc},             \"instruments\":[\"SOLUSDT\"],\"binlog_files\":[{}]}}",
            files.join(",")
        );
        std::fs::write(dir.join("session.json"), json).unwrap();
    }

    /// Каталог с частями за двое суток: верхний `started_utc`/`start_hour_utc`
    /// — от последней сессии (D+1, 14 ч), но каждая часть получает свои
    /// сутки из имени файла и свой час из `binlog_files`.
    #[test]
    fn session_parts_for_attributes_day_and_hour_per_part_not_per_directory() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let d1p1 = crate::commands::record::day_file_path(root, "SOLUSDT", "2026-09-08", 1);
        let d1p2 = crate::commands::record::day_file_path(root, "SOLUSDT", "2026-09-08", 2);
        let d2p1 = crate::commands::record::day_file_path(root, "SOLUSDT", "2026-09-09", 1);
        for p in [&d2p1, &d1p2, &d1p1] {
            std::fs::write(p, b"stub").unwrap();
        }
        write_session_json(
            root,
            14,
            &[
                ("SOLUSDT", 1, "2026-09-08T02:00:00Z"),
                ("SOLUSDT", 2, "2026-09-08T11:30:00Z"),
                ("SOLUSDT", 1, "2026-09-09T14:00:00Z"),
            ],
        );
        let parts = session_parts_for(root, "SOLUSDT").unwrap();
        let view: Vec<(&str, u32, u32)> = parts
            .iter()
            .map(|p| (p.day_utc.as_str(), p.part, p.start_hour_utc))
            .collect();
        // Часть 1 встречается дважды (D и D+1) — запись `binlog_files`
        // подбирается по тройке (symbol, part, сутки `started_utc`), не по
        // паре: у части 1 суток D+1 час 14, не 02.
        assert_eq!(
            view,
            vec![
                ("2026-09-08", 1, 2),
                ("2026-09-08", 2, 11),
                ("2026-09-09", 1, 14)
            ]
        );
        assert_eq!(
            parts.iter().map(|p| p.path.clone()).collect::<Vec<_>>(),
            vec![d1p1, d1p2, d2p1],
            "порядок тот же, что у session_binlog_for"
        );
    }

    /// Запись до таска 22: `session.json` без `binlog_files` — час берётся
    /// из `start_hour_utc` каталога, сутки по-прежнему из имени файла.
    #[test]
    fn session_parts_for_falls_back_to_directory_hour_without_binlog_files() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(root.join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
        std::fs::write(
            root.join("session.json"),
            "{\"started_utc\":\"2026-09-08T05:00:00Z\",\"start_hour_utc\":5,\"instruments\":[\"SOLUSDT\"]}",
        )
        .unwrap();
        let parts = session_parts_for(root, "SOLUSDT").unwrap();
        assert_eq!(parts.len(), 1);
        assert_eq!(parts[0].day_utc, "2026-09-08");
        assert_eq!(parts[0].start_hour_utc, 5);
        assert_eq!(parts[0].part, 1);
    }

    #[test]
    fn session_parts_for_requires_session_json() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
        let err = session_parts_for(dir.path(), "SOLUSDT").unwrap_err();
        assert!(
            err.to_string().contains("session.json"),
            "без session.json — не сессия: {err}"
        );
    }

    #[test]
    fn group_parts_by_day_keeps_order_and_splits_only_on_day_change() {
        let part = |day: &str, n: u32| SessionPart {
            path: PathBuf::from(format!("SOLUSDT-{day}-p{n}.binlog")),
            part: n,
            day_utc: day.to_string(),
            start_hour_utc: 0,
        };
        let groups = group_parts_by_day(vec![
            part("2026-09-08", 1),
            part("2026-09-08", 2),
            part("2026-09-09", 1),
        ]);
        let view: Vec<(&str, Vec<u32>)> = groups
            .iter()
            .map(|(d, ps)| (d.as_str(), ps.iter().map(|p| p.part).collect()))
            .collect();
        assert_eq!(
            view,
            vec![("2026-09-08", vec![1, 2]), ("2026-09-09", vec![1])]
        );
    }

    #[test]
    fn session_days_in_dir_unions_dated_parts_of_all_symbols_and_ignores_the_rest() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(root.join("session.json"), "{}").unwrap();
        for name in [
            "SOLUSDT-2026-09-08.binlog",
            "SOLUSDT-2026-09-08-p2.binlog",
            "NEARUSDT-2026-09-10.binlog",
            "ZECUSDT.binlog", // старый недатированный формат
            "gaps.csv",
            "BAD-2026-9-8.binlog", // не YYYY-MM-DD
        ] {
            std::fs::write(root.join(name), b"stub").unwrap();
        }
        let days: Vec<String> = session_days_in_dir(root).into_iter().collect();
        assert_eq!(days, vec!["2026-09-08", "2026-09-10"]);
    }

    #[test]
    fn session_days_in_dir_is_empty_without_session_json() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
        assert!(session_days_in_dir(dir.path()).is_empty());
    }

    #[test]
    fn day_of_binlog_name_parses_dated_names_only() {
        assert_eq!(
            day_of_binlog_name("SOLUSDT-2026-09-08.binlog"),
            Some("2026-09-08")
        );
        assert_eq!(
            day_of_binlog_name("SOLUSDT-2026-09-08-p12.binlog"),
            Some("2026-09-08")
        );
        assert_eq!(day_of_binlog_name("SOLUSDT.binlog"), None);
        assert_eq!(day_of_binlog_name("2026-09-08.binlog"), None, "без символа");
        assert_eq!(day_of_binlog_name("SOLUSDT-2026-09-08.csv"), None);
    }

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
            "backtest",
            "clock",
            "dashboard",
            "export",
            "levels",
            "markout",
            "pick",
            "pilot",
            "power",
            "probe",
            "profiles",
            "react",
            "record",
            "session",
            "shortlist",
            "verify",
            "watch",
        ];
        assert_eq!(
            sorted,
            expected.iter().map(ToString::to_string).collect::<Vec<_>>()
        );
    }
}
