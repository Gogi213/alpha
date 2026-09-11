//! `lob shortlist` — механизм третьей заморозки задачи (таск 12, история
//! 24–28, 42; `spec.md` §9; `PLAN.md` раздел 9; `SETTLED.md` В-19 Decision 27,
//! В-29). Сам перебор, сплит, заморозка и подтверждающая таблица — уже
//! построены (`crate::lob::shortlist`, таск 06); этот файл — тонкая CLI-
//! обвязка, которая соединяет их с уже посчитанными сессиями (`lob session`,
//! `lob profiles`) и превращает «заморозка коммитом» из обещания в механизм.
//!
//! # Что делает один прогон
//!
//! 1. **Граница по календарю до анализа.** Сутки собираются из
//!    `session.json` каждой сессии под `--root`; `crate::lob::shortlist::
//!    split_calendar` делит их 60/40. Первый прогон **пишет** границу в
//!    `--preregistration` (по умолчанию `docs/findings/
//!    shortlist-boundary-preregistration.md`); каждый следующий прогон
//!    **читает** её обратно, не пересчитывая, — граница решена один раз, до
//!    того, как посчитан хоть один профиль, и назначенные сутки не двигаются
//!    заново, даже если под `--root` появились новые сутки (`SETTLED.md`
//!    В-29: этап «после пилота, до первой сессии сбора»). Сутки, которых нет
//!    в файле предрегистрации, в анализ этим прогоном не попадают — это
//!    консервативнее, чем гадать, к какой половине их приписать.
//! 2. **Разведочная таблица.** `run_profiles_with_fill_model`
//!    (`commands::lob::profiles`, таск 10) — без правок, тем же кодом, что
//!    `lob profiles`, с моделью исполнения от `resolve_fill_model` (таск 16:
//!    `BacktestFillModel`, если задана тройка RTT/лота, иначе `NoFillModel`,
//!    как раньше) — прогоняется на подмножестве сессий, чьи сутки входят в
//!    разведочную половину. Модуль
//!    сканирует **все** подкаталоги переданного `--root` как сессии и зоны
//!    этого таска не может править, поэтому дневной фильтр — не правка
//!    `profiles.rs`, а отдельный каталог-времянка с жёсткими ссылками
//!    (`ScratchRoot`/`build_filtered_root`) только на нужные сутки плюс
//!    копия `instruments.csv`. Этот прогон боевой (`allow_unverified =
//!    false`) и поэтому пишет `runs.csv` — строка на каждый id сетки
//!    (`shortlist::log_profile_trials`, дергается изнутри `profiles.rs`), и
//!    это единственное место, где в журнал попадают испытания: подтверждающая
//!    ниже не считается новым испытанием (Decision 27 — испытания это
//!    перебор, а не его проверка).
//! 3. **Отбор.** `n >= 100` на разведочной (`shortlist::select_shortlist`) —
//!    единственный критерий; `net`/`net_fill` в отборе не участвуют, потому
//!    что без модели исполнения (`NoFillModel`) они не измерены (см. ниже).
//! 4. **Заморозка.** `--freeze-commit` — обязательный флаг без умолчания:
//!    вызывающий называет коммит, которым фиксирует список (изобретать его
//!    самому нельзя, §9). `shortlist::freeze_shortlist` считает отпечаток;
//!    `--freeze-out` (по умолчанию `docs/findings/shortlist-frozen.txt`,
//!    коммитится) — машиночитаемый список id по одному на строку, который
//!    читает `lob markout --confirmatory --shortlist <path>` перед тем как
//!    впустить профиль на подтверждающую (`markout.rs`,
//!    `require_shortlist_member`) — критерий приёмки таска буквально: без
//!    файла или с профилем не из списка команда отказывает ненулевым кодом.
//! 5. **Подтверждающая.** Тот же `run_profiles_with_fill_model` на
//!    подмножестве подтверждающих суток, но `runs_out` указывает на
//!    времянку — это не новое испытание. Таблица кормит
//!    `shortlist::confirmatory_table`/`decide_verdict`: профиль остаётся
//!    строкой отчёта, даже когда на подтверждающей его нет вовсе (n=0)
//!    — не исчезает (Decision 27, п. «профиль… идёт в отчёт как не
//!    подтверждённый»).
//!
//! # `G` на подтверждающей — реален (таск 13, было CONCERNS у таска 12)
//!
//! `decide_profile` (`crate::lob::shortlist`) зачитывает профиль только при
//! `n >= 100` **и** `G >= G_MIN` — число годных суток, различных для этого
//! конкретного профиля. `docs/findings/profiles-*.csv` (`profiles.rs`) несёт
//! колонку `g`, дописанную в конец шапки (ремонт по ревью таска 12,
//! открытый пункт (1)): число различных суток, на которые пришлось хотя бы
//! одно наблюдение профиля (`ProfileAgg::days`). Этот файл читает её через
//! `ProfilesTableRow::g` и подставляет в `ConfProfile`/`ExplProfile` вместо
//! прежнего захардкоженного нуля.
//!
//! # DSR по фактическому `N` — порог больше не «выше нуля» (таск 13)
//!
//! `decide_profile` требует не только `net_fill_lower > 0`, но и
//! `observed_sharpe >= required_sharpe_for_dsr(total_trials, n, DSR_TARGET)`
//! (R47, `final_metrics`). `total_trials` здесь — фактические строки
//! `runs.csv` (`trials_from_runs_csv`), не номинал сетки: журнал копится
//! через прогоны, и второй боевой прогон честно видит больше испытаний, чем
//! первый (тест `production_run_freezes_boundary_once_and_writes_frozen_
//! list`).
//!
//! # `fill`/`net_fill`/`observed_sharpe` — модель исполнения (таск 16)
//!
//! `resolve_fill_model` (`commands::lob::profiles`) решает, что подставить в
//! `run_profiles_with_fill_model` из тройки `--median-rtt-ns`/
//! `--p95-rtt-ns`/`--order-qty-e9`: заданы все три — `BacktestFillModel`
//! поверх `lob::backtest` (три денежные колонки и `observed_sharpe` —
//! реальные числа, `confirmed` достижим через `decide_profile`); не задан ни
//! один — `NoFillModel`, как в тасках 10–13 (`not_measured`/`None`,
//! `confirmed` недостижим). `net_bps` (после издержек, без веса на
//! исполнение) в таблице печатается всегда независимо от модели — то, что
//! называет ticket «отбор по net»: человек, читающий `shortlist-<дата>.md`,
//! видит `net_bps` и может судить о профиле сам, даже без модели исполнения.
//!
//! # Тест на час суток — подключён (таск 13, было CONCERNS у таска 12)
//!
//! `shortlist::hour_dependence_test`/`log_hour_test` (таск 06) вызывает
//! `profiles.rs` — по одной строке `runs.csv` на профиль, у которого тест
//! состоялся (ремонт по ревью таска 12, открытый пункт (2)); методический
//! отказ теста (суток меньше `G_MIN`, сетка Уэбба грубее альфы, наблюдаемая
//! вырождена) не пишется, как и непригодная корзина сетки. `total_trials`
//! этого файла берёт фактические строки `runs.csv`, поэтому часовые тесты
//! уже учтены без отдельного параметра.
//!
//! # Отладочный режим (`--allow-unverified`)
//!
//! Пропускает границу/заморозку/подтверждающую целиком: один период — делить
//! не на что (`shortlist::split_calendar` и не позвал бы меньше двух суток).
//! Пишет `data/shortlist-debug/shortlist-<дата>.md` (не `docs/findings/`,
//! ticket п.5) с меткой `debug`, не трогает `--runs-out`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use clap::Args;

use crate::commands::record::instruments_csv_path;
use crate::lob::final_metrics;
use crate::lob::shortlist::{
    best_confirmed_net_fill, build_profile_grid, confirmatory_table, decide_verdict,
    freeze_shortlist, select_shortlist, split_calendar, trials_from_runs_csv, write_shortlist_md,
    CalendarSplit, ConfProfile, ConfirmStatus, ExplProfile, FrozenShortlist, InstrumentCoverage,
    VerdictHeader,
};
use crate::stats;

use super::levels::H3ModeArg;
use super::profiles::{resolve_fill_model, run_profiles_with_fill_model, ProfilesArgs};
use super::{DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS};

// ---------------------------------------------------------------------------
// CLI.
// ---------------------------------------------------------------------------

/// Аргументы `lob shortlist`. `--h3-mode` обязателен без умолчания — тот же
/// выбор, что у `levels`/`markout`/`watch`/`pilot`/`profiles`.
#[derive(Debug, Args)]
pub struct ShortlistArgs {
    /// Корень: `instruments.csv` пула плюс подкаталог на каждую сессию
    /// (`lob session`) — тот же вход, что `lob profiles`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Полная таблица кандидатов (`lob pick`) — источник `coverage_top50_bps`.
    #[arg(long, default_value = "docs/plan/candidates.csv")]
    pub candidates_csv: PathBuf,
    /// Режим порога H3: `floor` | `percentile`, без умолчания.
    #[arg(long)]
    pub h3_mode: H3ModeArg,
    /// Порог рождения H3 в лотах: только режим `percentile`.
    #[arg(long)]
    pub h3_lots: Option<i64>,
    /// Прогрев в мс, на сессию.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Отладочный вход: один период, без границы/заморозки/подтверждающей
    /// (см. doc модуля). Пишет `data/shortlist-debug/…`, не `docs/findings/`.
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
    /// Куда писать отчёт (по умолчанию `docs/findings/shortlist-<дата>.md`,
    /// в отладке — `data/shortlist-debug/shortlist-<дата>.md`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Момент для имени файла и шапки UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
    /// Журнал испытаний (боевой режим пишет туда сетку разведочной один раз).
    #[arg(long, default_value = "docs/plan/runs.csv")]
    pub runs_out: PathBuf,
    /// Файл-предрегистрация границы разведочная/подтверждающая (см. doc
    /// модуля, п.1): первый прогон пишет, следующие — читают.
    #[arg(
        long,
        default_value = "docs/findings/shortlist-boundary-preregistration.md"
    )]
    pub preregistration: PathBuf,
    /// Куда писать замороженный список id, по одному на строку (читает
    /// `lob markout --confirmatory --shortlist`).
    #[arg(long, default_value = "docs/findings/shortlist-frozen.txt")]
    pub freeze_out: PathBuf,
    /// Хеш коммита заморозки. Обязателен без умолчания в боевом режиме
    /// (изобретённое число запрещено, §9): вызывающий называет коммит,
    /// которым фиксирует шорт-лист.
    #[arg(long)]
    pub freeze_commit: Option<String>,
    /// Медианная замеренная RTT исполнения, нс — та же тройка, что `lob
    /// profiles`/`lob backtest` (`resolve_fill_model`, таск 16): заданы все
    /// три — разведочная/подтверждающая гоняются с `BacktestFillModel`
    /// (`observed_sharpe`/`fill`/`net_fill` становятся числами, DSR в
    /// вердикте зачитывается); не задан ни один — `NoFillModel`, как раньше.
    #[arg(long)]
    pub median_rtt_ns: Option<i64>,
    /// 95-й перцентиль той же замеренной RTT — см. `median_rtt_ns`.
    #[arg(long)]
    pub p95_rtt_ns: Option<i64>,
    /// Размер круга в 1e-9 лотов (Decision 22) — см. `median_rtt_ns`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
}

/// Итог `lob shortlist` для печати диспетчером.
#[derive(Debug)]
pub struct ShortlistSummary {
    pub out: PathBuf,
    pub debug: bool,
    pub trials: usize,
    pub shortlisted: usize,
    pub verdict: Option<String>,
}

// ---------------------------------------------------------------------------
// Каталог сессий и сутки — тот же приём, что `profiles.rs`/`watch.rs`
// (частные копии в каждом файле; ни один не `pub`, переиспользовать нечего).
// ---------------------------------------------------------------------------

#[derive(Debug, serde::Deserialize)]
struct SessionMetaPeek {
    started_utc: String,
}

fn read_session_meta(dir: &Path) -> Option<SessionMetaPeek> {
    let text = std::fs::read_to_string(dir.join("session.json")).ok()?;
    serde_json::from_str(&text).ok()
}

fn session_dirs(root: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let entries = std::fs::read_dir(root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", root.display()))?;
    let mut dirs = Vec::new();
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

/// Группирует подкаталоги сессий по суткам `started_utc`. Каталоги без
/// `session.json` или без разборного `started_utc` молча пропускаются — не
/// сессия, не ошибка (тот же приём, что `profiles.rs`/`watch.rs`).
fn group_by_day(dirs: &[PathBuf]) -> BTreeMap<String, Vec<PathBuf>> {
    let mut out: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
    for dir in dirs {
        let Some(meta) = read_session_meta(dir) else {
            continue;
        };
        let day = meta.started_utc.get(..10).unwrap_or_default().to_string();
        if day.is_empty() {
            continue;
        }
        out.entry(day).or_default().push(dir.clone());
    }
    out
}

// ---------------------------------------------------------------------------
// Пул и покрытие — та же пара читателей, что `profiles.rs` (зона таска не
// пускает переиспользовать приватные функции того файла напрямую).
// ---------------------------------------------------------------------------

#[derive(Debug, serde::Deserialize)]
struct PoolSymbolRow {
    symbol: String,
}

fn read_pool_symbols(instruments_csv: &Path) -> anyhow::Result<Vec<String>> {
    let mut r = super::pick::instruments_csv_reader(instruments_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
    let mut out = Vec::new();
    for row in r.deserialize::<PoolSymbolRow>() {
        out.push(row?.symbol);
    }
    anyhow::ensure!(!out.is_empty(), "{}: пул пуст", instruments_csv.display());
    out.sort();
    Ok(out)
}

#[derive(Debug, serde::Deserialize)]
struct CoverageRow {
    symbol: String,
    coverage_top50_bps: Option<f64>,
}

fn read_coverage(
    candidates_csv: &Path,
    pool: &[String],
) -> anyhow::Result<Vec<InstrumentCoverage>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(candidates_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", candidates_csv.display()))?;
    let mut by_symbol: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
    for row in r.deserialize::<CoverageRow>() {
        let row = row?;
        if let Some(c) = row.coverage_top50_bps {
            by_symbol.insert(row.symbol, c);
        }
    }
    pool.iter()
        .map(|s| {
            by_symbol
                .get(s)
                .copied()
                .map(|coverage_bps| InstrumentCoverage {
                    symbol: s.clone(),
                    coverage_bps,
                })
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "{}: нет покрытия top50 для {s} — lob pick обязан измерить пул раньше",
                        candidates_csv.display()
                    )
                })
        })
        .collect()
}

#[derive(Debug, serde::Deserialize)]
struct OrderSizeRow {
    symbol: String,
    order_size_notional_usd_e9: Option<i64>,
}

/// Размер ордера в долларах на символ (`candidates.csv`, Decision 22) —
/// справочные долларовые колонки шапки (R54), ни в один гейт не входят.
/// Символ без строки или без измеренного размера просто отсутствует в карте
/// (не отказ): доллар — необязательная справка, а не критерий.
fn read_order_size_usd(
    candidates_csv: &Path,
    pool: &[String],
) -> anyhow::Result<BTreeMap<String, f64>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(candidates_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", candidates_csv.display()))?;
    let mut out = BTreeMap::new();
    for row in r.deserialize::<OrderSizeRow>() {
        let row = row?;
        if let Some(usd_e9) = row.order_size_notional_usd_e9 {
            if pool.contains(&row.symbol) {
                #[allow(clippy::cast_precision_loss)]
                out.insert(row.symbol, usd_e9 as f64 / 1e9);
            }
        }
    }
    Ok(out)
}

/// Извлекает единственный символ из id сетки — только `cross:<SYM>|…` и
/// `marginal:instrument=<SYM>` привязаны к одному инструменту; остальные
/// маргиналы (`side`/`outcome`/`size`/`life`/`repeat`) размера ордера не
/// имеют по построению (`None`, не отказ).
fn symbol_from_profile_id(id: &str) -> Option<&str> {
    if let Some(rest) = id.strip_prefix("cross:") {
        return rest.split('|').next();
    }
    id.strip_prefix("marginal:instrument=")
}

// ---------------------------------------------------------------------------
// Каталог-времянка: подмножество суток под отдельным корнем, чтобы
// `run_profiles_with_fill_model` (сканирует все подкаталоги `--root`) увидел
// только нужную половину календаря. `tempfile` — только `dev-dependencies`
// (закрытый список §1), в проде недоступен, поэтому времянка ручная.
// ---------------------------------------------------------------------------

static SCRATCH_COUNTER: AtomicU64 = AtomicU64::new(0);

struct ScratchRoot(PathBuf);

impl ScratchRoot {
    fn new(label: &str) -> anyhow::Result<Self> {
        let n = SCRATCH_COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!(
            "alpha-lob-shortlist-{}-{label}-{n}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir)
            .map_err(|e| anyhow::anyhow!("времянка {}: {e}", dir.display()))?;
        Ok(Self(dir))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for ScratchRoot {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn link_or_copy(src: &Path, dst: &Path) -> anyhow::Result<()> {
    if let Some(parent) = dst.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    if std::fs::hard_link(src, dst).is_err() {
        std::fs::copy(src, dst)
            .map_err(|e| anyhow::anyhow!("{} -> {}: {e}", src.display(), dst.display()))?;
    }
    Ok(())
}

fn link_dir_tree(src: &Path, dst: &Path) -> anyhow::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let path = entry.path();
        let target = dst.join(entry.file_name());
        if path.is_dir() {
            link_dir_tree(&path, &target)?;
        } else {
            link_or_copy(&path, &target)?;
        }
    }
    Ok(())
}

/// Строит времянку с `instruments.csv` пула плюс только теми подкаталогами
/// сессий, чьи сутки входят в `wanted_days`.
fn build_filtered_root(
    dest_root: &Path,
    instruments_csv_src: &Path,
    by_day: &BTreeMap<String, Vec<PathBuf>>,
    wanted_days: &[String],
) -> anyhow::Result<()> {
    link_or_copy(instruments_csv_src, &instruments_csv_path(dest_root))?;
    for day in wanted_days {
        let Some(dirs) = by_day.get(day) else {
            continue;
        };
        for dir in dirs {
            let name = dir
                .file_name()
                .ok_or_else(|| anyhow::anyhow!("каталог сессии без имени: {}", dir.display()))?;
            link_dir_tree(dir, &dest_root.join(name))?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Граница разведочная/подтверждающая: пишется один раз, читается дальше.
// ---------------------------------------------------------------------------

fn split_csv_list(s: &str) -> Vec<String> {
    s.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// Читает файл предрегистрации, если он уже есть (граница заморожена — не
/// пересчитывать); иначе делит переданные сутки 60/40 и пишет файл. Сутки,
/// добавившиеся под `--root` после первой записи, в возвращённый сплит не
/// попадают: граница решена один раз, до анализа (doc модуля, п.1).
fn load_or_write_boundary(path: &Path, days: &[String]) -> anyhow::Result<CalendarSplit> {
    if path.is_file() {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        let mut exploratory = Vec::new();
        let mut confirmatory = Vec::new();
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("exploratory: ") {
                exploratory = split_csv_list(rest);
            } else if let Some(rest) = line.strip_prefix("confirmatory: ") {
                confirmatory = split_csv_list(rest);
            }
        }
        anyhow::ensure!(
            !exploratory.is_empty() && !confirmatory.is_empty(),
            "{}: файл предрегистрации не разобрался (нет строк exploratory:/confirmatory:)",
            path.display()
        );
        return Ok(CalendarSplit {
            exploratory,
            confirmatory,
        });
    }
    let split = split_calendar(days).map_err(|e| anyhow::anyhow!("{e}"))?;
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let text = format!(
        "# lob shortlist: граница разведочная/подтверждающая по календарю,\n\
         # назначена до анализа и заморожена (Decision 27, SETTLED.md В-29 —\n\
         # этап «после пилота, до первой сессии сбора»). Файл не переписывается,\n\
         # пока не удалён вручную — это и есть заморозка границы.\n\
         exploratory: {}\n\
         confirmatory: {}\n",
        split.exploratory.join(","),
        split.confirmatory.join(","),
    );
    std::fs::write(path, text)?;
    Ok(split)
}

// ---------------------------------------------------------------------------
// Чтение уже посчитанной таблицы профилей (формат `profiles.rs::HEADER`) —
// по имени колонки, тем же приёмом, что `backtest.rs::{ProfilesTableRow,
// read_table}`: `csv`+`serde` матчит по заголовку, лишние колонки (доли
// исходов, горизонты, `session_start_hours_utc`, …) не мешают и порядок
// колонок в `profiles.rs::HEADER` менять не нужно для этого файла отдельно.
// ---------------------------------------------------------------------------

/// Строка `docs/findings/profiles-*.csv` (`commands::lob::profiles`), только
/// нужные этому файлу колонки. Денежные колонки читаются строкой:
/// `none`/`not_measured` — не `f64`, а два разных отсутствия (см.
/// `parse_measured_f64`).
#[derive(Debug, Clone, serde::Deserialize)]
struct ProfilesTableRow {
    profile_id: String,
    n: u64,
    net_fill: String,
    net_fill_lower: String,
    /// Годные сутки на профиль (ремонт по ревью таска 12, открытый пункт (1)
    /// для таска 13) — `profiles.rs` дописывает колонку в конец шапки
    /// (`HEADER`), матч здесь по имени, не по позиции, старые файлы без
    /// колонки не читались бы этим полем без `default`.
    #[serde(default)]
    g: u64,
    /// Наблюдаемый Шарп исполнившихся входов (таск 16) — та же колонка,
    /// дописанная `profiles.rs` следом за `g`; `#[serde(default)]` — файлы
    /// таска 13 без неё читаются как `""`, что `parse_measured_f64` уже
    /// понимает как отсутствие числа.
    #[serde(default)]
    observed_sharpe: String,
}

struct ProfileNums {
    n: u64,
    g: u64,
    net_fill: Option<f64>,
    net_fill_lower: Option<f64>,
    observed_sharpe: Option<f64>,
}

fn parse_measured_f64(s: &str) -> Option<f64> {
    match s {
        "" | "none" | "not_measured" => None,
        _ => s.parse::<f64>().ok(),
    }
}

fn read_profile_table(path: &Path) -> anyhow::Result<BTreeMap<String, ProfileNums>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let mut out = BTreeMap::new();
    for row in r.deserialize::<ProfilesTableRow>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        out.insert(
            row.profile_id,
            ProfileNums {
                n: row.n,
                g: row.g,
                net_fill: parse_measured_f64(&row.net_fill),
                net_fill_lower: parse_measured_f64(&row.net_fill_lower),
                observed_sharpe: parse_measured_f64(&row.observed_sharpe),
            },
        );
    }
    Ok(out)
}

fn run_profiles_over(
    root: PathBuf,
    runs_out: PathBuf,
    out: PathBuf,
    allow_unverified: bool,
    base: &ShortlistArgs,
) -> anyhow::Result<PathBuf> {
    let profiles_args = ProfilesArgs {
        root,
        candidates_csv: base.candidates_csv.clone(),
        h3_mode: base.h3_mode,
        h3_lots: base.h3_lots,
        warmup_ms: base.warmup_ms,
        repeat_window_ms: base.repeat_window_ms,
        allow_unverified,
        out: Some(out),
        now_utc: base.now_utc.clone(),
        runs_out,
        median_rtt_ns: base.median_rtt_ns,
        p95_rtt_ns: base.p95_rtt_ns,
        order_qty_e9: base.order_qty_e9,
    };
    // Та же тройка RTT/лота, та же модель — на разведочной, подтверждающей и
    // отладке (таск 16, `resolve_fill_model` — общая точка с `lob profiles`).
    let model = resolve_fill_model(base.median_rtt_ns, base.p95_rtt_ns, base.order_qty_e9)?;
    let summary = run_profiles_with_fill_model(&profiles_args, model.as_ref())?;
    Ok(summary.out)
}

// ---------------------------------------------------------------------------
// Замороженный список: формат файла для `lob markout --confirmatory`.
// ---------------------------------------------------------------------------

fn write_frozen_shortlist_file(path: &Path, frozen: &FrozenShortlist) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut text = String::new();
    text.push_str(&format!("# freeze_commit: {}\n", frozen.commit()));
    text.push_str(&format!("# trials: {}\n", frozen.trials()));
    text.push_str(&format!("# fingerprint: {}\n", frozen.fingerprint_hex()));
    for id in frozen.ids() {
        text.push_str(id);
        text.push('\n');
    }
    std::fs::write(path, text)?;
    Ok(())
}

fn read_frozen_shortlist_ids(path: &Path) -> anyhow::Result<Vec<String>> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — заморозка обязана предшествовать чтению подтверждающей (Decision 27/28)",
            path.display()
        )
    })?;
    Ok(text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_string)
        .collect())
}

/// Требует членство `profile_id` в замороженном шорт-листе. Вызывает `lob
/// markout --confirmatory` (`commands/lob/markout.rs`, единственная правка
/// таска 12 в этом файле) рядом с `require_session_ready_flag`: без файла
/// (заморозки ещё не было) или с профилем не из списка — отказ, эквивалент
/// ненулевого кода команды (критерий приёмки таска буквально).
pub fn require_shortlist_member(path: &Path, profile_id: &str) -> anyhow::Result<()> {
    let ids = read_frozen_shortlist_ids(path)?;
    anyhow::ensure!(
        ids.iter().any(|id| id == profile_id),
        "профиль {profile_id} вне замороженного шорт-листа {}",
        path.display()
    );
    Ok(())
}

// ---------------------------------------------------------------------------
// Отладочный отчёт: один период, без границы/заморозки/подтверждающей.
// ---------------------------------------------------------------------------

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.4}"),
        None => "not_measured".to_string(),
    }
}

fn write_debug_report(
    path: &Path,
    date: &str,
    trials: usize,
    ids: &[String],
    table: &BTreeMap<String, ProfileNums>,
) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut text = String::new();
    text.push_str(&format!(
        "# lob shortlist (debug): {date} — не данные, только отладка\n"
    ));
    text.push_str(&format!("trials: {trials}\n"));
    text.push_str(&format!("shortlisted (n>=100): {}\n", ids.len()));
    text.push_str("confirmatory: пропущена — один период, делить не на что\n");
    text.push_str("| profile_id | n | net_fill | net_fill_lower |\n");
    for (id, nums) in table {
        text.push_str(&format!(
            "| {id} | {} | {} | {} |\n",
            nums.n,
            fmt_opt(nums.net_fill),
            fmt_opt(nums.net_fill_lower)
        ));
    }
    std::fs::write(path, text)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Точка входа.
// ---------------------------------------------------------------------------

fn run_shortlist_debug(
    args: &ShortlistArgs,
    date: &str,
    grid: &[String],
) -> anyhow::Result<ShortlistSummary> {
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("data/shortlist-debug/shortlist-{date}.md")));
    let mut profiles_out = out.clone();
    profiles_out.set_extension("profiles.csv");
    let scratch_runs = ScratchRoot::new("debug-runs")?;
    let csv_path = run_profiles_over(
        args.root.clone(),
        scratch_runs.path().join("runs.csv"),
        profiles_out,
        true,
        args,
    )?;
    let table = read_profile_table(&csv_path)?;
    let expl_profiles: Vec<ExplProfile> = grid
        .iter()
        .map(|id| ExplProfile {
            id: id.clone(),
            n: table.get(id).map(|p| p.n).unwrap_or(0),
            g: 0,
        })
        .collect();
    let ids = select_shortlist(&expl_profiles);
    write_debug_report(&out, date, grid.len(), &ids, &table)?;
    Ok(ShortlistSummary {
        out,
        debug: true,
        trials: grid.len(),
        shortlisted: ids.len(),
        verdict: None,
    })
}

/// Точка входа `lob shortlist` (таск 12, CLI). См. doc модуля для механики
/// каждого шага и открытых мест (`G` на подтверждающей, час суток).
pub fn run_shortlist(args: &ShortlistArgs) -> anyhow::Result<ShortlistSummary> {
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let date = now.get(..10).unwrap_or("1970-01-01").to_string();

    let instruments_csv = instruments_csv_path(&args.root);
    let pool = read_pool_symbols(&instruments_csv)?;
    let coverages = read_coverage(&args.candidates_csv, &pool)?;
    let grid = build_profile_grid(&coverages).map_err(|e| anyhow::anyhow!("{e}"))?;

    if args.allow_unverified {
        return run_shortlist_debug(args, &date, &grid);
    }

    let dirs = session_dirs(&args.root)?;
    let by_day = group_by_day(&dirs);
    let days: Vec<String> = by_day.keys().cloned().collect();
    let split = load_or_write_boundary(&args.preregistration, &days)?;

    // Разведочная: боевой прогон — пишет runs.csv (испытания реальны),
    // включая тесты на час (`profiles.rs`, ремонт по ревью таска 12,
    // открытый пункт (2)). Число испытаний для DSR (R47) — фактические
    // строки журнала, а не номинал сетки: `trials_from_runs_csv`, не
    // `total_trials(grid.len(), 0)`.
    let expl_scratch = ScratchRoot::new("expl")?;
    build_filtered_root(
        expl_scratch.path(),
        &instruments_csv,
        &by_day,
        &split.exploratory,
    )?;
    let expl_csv = run_profiles_over(
        expl_scratch.path().to_path_buf(),
        args.runs_out.clone(),
        expl_scratch.path().join("profiles-exploratory.csv"),
        false,
        args,
    )?;
    let expl_table = read_profile_table(&expl_csv)?;
    let trials = trials_from_runs_csv(&args.runs_out).ok_or_else(|| {
        anyhow::anyhow!(
            "{}: журнал испытаний не читается — DSR не из чего дефлировать",
            args.runs_out.display()
        )
    })?;
    let expl_profiles: Vec<ExplProfile> = grid
        .iter()
        .map(|id| ExplProfile {
            id: id.clone(),
            n: expl_table.get(id).map(|p| p.n).unwrap_or(0),
            g: expl_table.get(id).map_or(0, |p| p.g),
        })
        .collect();
    let ids = select_shortlist(&expl_profiles);

    let commit = args.freeze_commit.clone().ok_or_else(|| {
        anyhow::anyhow!("--freeze-commit обязателен: заморозка коммитом (Decision 27/28)")
    })?;
    let frozen = freeze_shortlist(&ids, &commit, trials);
    write_frozen_shortlist_file(&args.freeze_out, &frozen)?;

    // Подтверждающая: времянка на runs_out — не новое испытание (Decision 27).
    let conf_scratch = ScratchRoot::new("conf")?;
    build_filtered_root(
        conf_scratch.path(),
        &instruments_csv,
        &by_day,
        &split.confirmatory,
    )?;
    let conf_runs_scratch = ScratchRoot::new("conf-runs")?;
    let conf_csv = run_profiles_over(
        conf_scratch.path().to_path_buf(),
        conf_runs_scratch.path().join("runs.csv"),
        conf_runs_scratch.path().join("profiles-confirmatory.csv"),
        false,
        args,
    )?;
    let conf_table = read_profile_table(&conf_csv)?;
    // `G` реален (ремонт по ревью таска 12, открытый пункт (1)): `profiles.rs`
    // считает годные сутки на профиль и несёт их колонкой `g`. `observed_sharpe`
    // (таск 16) — та же таблица, следующая колонка: реальное число, когда
    // `--median-rtt-ns`/`--p95-rtt-ns`/`--order-qty-e9` включили
    // `BacktestFillModel` (`resolve_fill_model`); без тройки — по-прежнему
    // `None` (`NoFillModel`, `not_measured` в файле), и `decide_profile`
    // честно печатает `unconfirmed`, а не подделывает `confirmed`.
    let order_size_usd = read_order_size_usd(&args.candidates_csv, &pool)?;
    let conf_profiles: Vec<ConfProfile> = frozen
        .ids()
        .iter()
        .map(|id| {
            let nums = conf_table.get(id);
            ConfProfile {
                id: id.clone(),
                n: nums.map(|p| p.n).unwrap_or(0),
                g: nums.map_or(0, |p| p.g),
                net_fill: nums.and_then(|p| p.net_fill),
                net_fill_lower: nums.and_then(|p| p.net_fill_lower),
                observed_sharpe: nums.and_then(|p| p.observed_sharpe),
                order_size_usd: symbol_from_profile_id(id)
                    .and_then(|s| order_size_usd.get(s))
                    .copied(),
            }
        })
        .collect();
    let rows = confirmatory_table(Some(&frozen), &conf_profiles, trials)
        .map_err(|e| anyhow::anyhow!("{e}"))?;
    let verdict = decide_verdict(&rows);

    // DSR в шапке (таск 16): тот же профиль, что выносит `value_bps`
    // (`best_confirmed_net_fill` — лучший подтверждённый по `net_fill`), тем
    // же порогом, что `decide_profile` уже применил к нему
    // (`final_metrics::dsr_for_trial_count`, skew/kurtosis нормального ряда —
    // тот же нейтральный выбор, что `required_sharpe_for_dsr`, без пробных
    // Шарпов всех `N` испытаний). `None`, если подтверждённого профиля нет
    // или его Шарп/`n` не измерены.
    let best_confirmed_profile = rows
        .iter()
        .filter(|r| r.status == ConfirmStatus::Confirmed)
        .filter_map(|r| conf_profiles.iter().find(|p| p.id == r.id).map(|p| (r, p)))
        .max_by(|(a, _), (b, _)| {
            a.net_fill
                .unwrap_or(f64::NEG_INFINITY)
                .total_cmp(&b.net_fill.unwrap_or(f64::NEG_INFINITY))
        });
    let dsr = best_confirmed_profile.and_then(|(_, p)| {
        let sr = p.observed_sharpe?;
        final_metrics::dsr_for_trial_count(sr, p.n as usize, 0.0, 3.0, trials)
    });
    // PBO/CPCV процедуры отбора (R47: «проверяют процедуру отбора, а не
    // выбранный профиль») требуют матрицу «испытания × периоды»/ряд
    // покруговых net по всей разведочной сетке — таблица профилей несёт
    // только агрегаты (`n`, `net_fill`, `observed_sharpe`), не сырые
    // покруговые ряды по каждому из ~150+ испытаний. Собрать эту матрицу —
    // отдельный конвейер (агрегация `fill_obs` по общим периодам календаря на
    // каждый id сетки), которого таск 16 не строит: `None`, честно, а не
    // подмена оценкой одного профиля (CONCERNS).
    let pbo = None;
    let cpcv_oos_sharpe = None;

    // Джекнайф-по-суткам (A03): пересчитывает `best_confirmed_net_fill` на
    // подтверждающей без одних суток за раз (тем же `run_profiles_over`, во
    // времянку, не новое испытание — doc `final_metrics::jackknife_sensitivity`:
    // «вызывающий считает их отдельными прогонами»). Меньше двух суток на
    // подтверждающей — исключать не из чего, `None`.
    let jackknife = if split.confirmatory.len() >= 2 {
        let mut leave_one_out = Vec::new();
        for excluded in &split.confirmatory {
            let subset_days: Vec<String> = split
                .confirmatory
                .iter()
                .filter(|d| *d != excluded)
                .cloned()
                .collect();
            let loo_scratch = ScratchRoot::new(&format!("loo-{excluded}"))?;
            build_filtered_root(loo_scratch.path(), &instruments_csv, &by_day, &subset_days)?;
            let loo_runs_scratch = ScratchRoot::new(&format!("loo-runs-{excluded}"))?;
            let loo_csv = run_profiles_over(
                loo_scratch.path().to_path_buf(),
                loo_runs_scratch.path().join("runs.csv"),
                loo_runs_scratch.path().join("profiles-loo.csv"),
                false,
                args,
            )?;
            let loo_table = read_profile_table(&loo_csv)?;
            let loo_profiles: Vec<ConfProfile> = frozen
                .ids()
                .iter()
                .map(|id| {
                    let nums = loo_table.get(id);
                    ConfProfile {
                        id: id.clone(),
                        n: nums.map(|p| p.n).unwrap_or(0),
                        g: nums.map_or(0, |p| p.g),
                        net_fill: nums.and_then(|p| p.net_fill),
                        net_fill_lower: nums.and_then(|p| p.net_fill_lower),
                        observed_sharpe: nums.and_then(|p| p.observed_sharpe),
                        order_size_usd: symbol_from_profile_id(id)
                            .and_then(|s| order_size_usd.get(s))
                            .copied(),
                    }
                })
                .collect();
            if let Ok(loo_rows) = confirmatory_table(Some(&frozen), &loo_profiles, trials) {
                if let Some(v) = best_confirmed_net_fill(&loo_rows) {
                    leave_one_out.push((excluded.clone(), v));
                }
            }
        }
        final_metrics::jackknife_sensitivity(&leave_one_out)
    } else {
        None
    };

    // Шапка (критерий приёмки таска 13, числа — таск 16): значение вердикта,
    // DSR реален, когда есть подтверждённый профиль; PBO/CPCV — см. выше;
    // фактический `G` (максимум среди измеренных строк — тот, на котором
    // вердикт мог состояться), разрешение сетки Уэбба на этом `G`,
    // джекнайф-по-суткам — реален при ≥ 2 сутках подтверждающей.
    let g_for_header = rows
        .iter()
        .filter(|r| r.status != ConfirmStatus::InsufficientData)
        .map(|r| r.g)
        .max();
    let header = VerdictHeader {
        value_bps: best_confirmed_net_fill(&rows),
        dsr,
        pbo,
        cpcv_oos_sharpe,
        g: g_for_header,
        #[allow(clippy::cast_possible_truncation)]
        p_grid_resolution: g_for_header.map(|g| stats::webb_p_grid_resolution(g as u32)),
        jackknife,
    };

    let out = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("docs/findings/shortlist-{date}.md")));
    write_shortlist_md(&out, &date, &frozen, &rows, verdict, &header)
        .map_err(|e| anyhow::anyhow!("{e}"))?;

    Ok(ShortlistSummary {
        out,
        debug: false,
        trials,
        shortlisted: frozen.ids().len(),
        verdict: Some(verdict.to_string()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_instruments_csv(root: &Path, symbols: &[&str]) {
        let mut text =
            String::from("symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n");
        for symbol in symbols {
            text.push_str(&format!("{symbol},0.01,0.1,0.1,5,5\n"));
        }
        std::fs::write(instruments_csv_path(root), text).unwrap();
    }

    fn write_candidates_csv(path: &Path, rows: &[(&str, f64)]) {
        let mut text = String::from("symbol,coverage_top50_bps\n");
        for (symbol, coverage) in rows {
            text.push_str(&format!("{symbol},{coverage}\n"));
        }
        std::fs::write(path, text).unwrap();
    }

    fn write_session_dir(root: &Path, session_id: &str, symbol: &str, started_utc: &str) {
        let dir = root.join(session_id);
        std::fs::create_dir_all(&dir).unwrap();
        let json = format!("{{\"started_utc\":\"{started_utc}\",\"start_hour_utc\":2,\"instruments\":[\"{symbol}\"]}}");
        std::fs::write(dir.join("session.json"), json).unwrap();
        let header = crate::binlog::Header {
            tick_e9: super::super::test_support::FIX_TICK_E9,
            step_e9: 1_000_000,
            max_records_per_frame: 4096,
        };
        let frames = super::super::test_support::three_level_frames();
        let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
        for f in &frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        std::fs::write(dir.join(format!("{symbol}.binlog")), w.into_inner()).unwrap();
        std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok").unwrap();
    }

    fn base_args(root: &Path, candidates_csv: PathBuf) -> ShortlistArgs {
        ShortlistArgs {
            root: root.to_path_buf(),
            candidates_csv,
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            warmup_ms: 0,
            repeat_window_ms: super::super::DEFAULT_REPEAT_WINDOW_MS,
            allow_unverified: false,
            out: Some(root.join("shortlist.md")),
            now_utc: Some("2026-05-10T00:00:00Z".to_string()),
            runs_out: root.join("runs.csv"),
            preregistration: root.join("preregistration.md"),
            freeze_out: root.join("shortlist-frozen.txt"),
            freeze_commit: Some("deadbeef".to_string()),
            median_rtt_ns: None,
            p95_rtt_ns: None,
            order_qty_e9: None,
        }
    }

    /// Механизм отладки (п.5 таска): один период, `runs.csv` не трогается,
    /// подтверждающая пропущена, отчёт помечен `debug`.
    #[test]
    fn debug_run_skips_split_and_does_not_touch_runs_csv() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &["SOLUSDT"]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        write_session_dir(
            root,
            "2026-05-01T020000Z",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
        );

        let mut args = base_args(root, candidates_csv);
        args.allow_unverified = true;
        args.out = Some(root.join("shortlist-debug.md"));

        let summary = run_shortlist(&args).expect("отладочный прогон");
        assert!(summary.debug);
        assert!(summary.verdict.is_none());
        let text = std::fs::read_to_string(&summary.out).unwrap();
        assert!(text.contains("debug"), "{text}");
        assert!(text.contains("confirmatory: пропущена"), "{text}");
        assert!(!args.runs_out.exists(), "отладка не пишет runs.csv");
        assert!(
            !args.preregistration.exists(),
            "отладка не пишет предрегистрацию"
        );
    }

    /// Механизм п.1–2: граница пишется один раз до анализа и переиспользуется
    /// — третьи сутки, добавленные после первой записи, не двигают уже
    /// зафиксированную границу. Механизм п.4: заморозка пишет файл, который
    /// читает `require_shortlist_member`, и требует `--freeze-commit`.
    #[test]
    fn production_run_freezes_boundary_once_and_writes_frozen_list() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &["SOLUSDT"]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        write_session_dir(
            root,
            "2026-05-01T020000Z",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
        );
        write_session_dir(
            root,
            "2026-05-02T020000Z",
            "SOLUSDT",
            "2026-05-02T02:00:00Z",
        );

        let args = base_args(root, candidates_csv.clone());
        let summary = run_shortlist(&args).expect("первый боевой прогон");
        assert!(!summary.debug);
        assert!(summary.verdict.is_some());
        assert!(args.runs_out.exists(), "боевой режим пишет runs.csv");
        let rows_after_first = crate::lob::runs::read_run_rows(&args.runs_out)
            .unwrap()
            .len();
        assert_eq!(
            rows_after_first, summary.trials,
            "по строке журнала на каждый id сетки"
        );

        let boundary_text = std::fs::read_to_string(&args.preregistration).unwrap();
        assert!(
            boundary_text.contains("exploratory: 2026-05-01"),
            "{boundary_text}"
        );
        assert!(
            boundary_text.contains("confirmatory: 2026-05-02"),
            "{boundary_text}"
        );

        assert!(args.freeze_out.exists(), "заморозка пишет файл списка");
        let frozen_text = std::fs::read_to_string(&args.freeze_out).unwrap();
        assert!(
            frozen_text.contains("freeze_commit: deadbeef"),
            "{frozen_text}"
        );

        // Третьи сутки не должны сдвинуть уже зафиксированную границу.
        write_session_dir(
            root,
            "2026-05-03T020000Z",
            "SOLUSDT",
            "2026-05-03T02:00:00Z",
        );
        let summary2 = run_shortlist(&args).expect("второй прогон после новых суток");
        let boundary_text2 = std::fs::read_to_string(&args.preregistration).unwrap();
        assert_eq!(
            boundary_text, boundary_text2,
            "граница зафиксирована — новые сутки её не двигают"
        );
        let rows_after_second = crate::lob::runs::read_run_rows(&args.runs_out)
            .unwrap()
            .len();
        assert_eq!(
            rows_after_second,
            2 * summary.trials,
            "второй боевой прогон — ещё по строке на id сетки (append, не перезапись)"
        );
        // `trials` теперь идёт из фактических строк `runs.csv` (R47), а не
        // из номинала сетки: журнал копится через прогоны (append, не
        // перезапись), поэтому второй прогон честно видит вдвое больше
        // испытаний, чем первый — то самое «поправка по фактическому N»,
        // которое требует ticket 13, а не постоянное число от состава пула.
        assert_eq!(summary2.trials, 2 * summary.trials);
    }

    /// Без `--freeze-commit` боевой прогон обязан отказать: изобретать
    /// коммит самому запрещено (§9).
    #[test]
    fn production_run_requires_freeze_commit() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &["SOLUSDT"]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        write_session_dir(
            root,
            "2026-05-01T020000Z",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
        );
        write_session_dir(
            root,
            "2026-05-02T020000Z",
            "SOLUSDT",
            "2026-05-02T02:00:00Z",
        );
        let mut args = base_args(root, candidates_csv);
        args.freeze_commit = None;
        assert!(run_shortlist(&args).is_err());
    }

    /// Механизм заморозки как проверяет `lob markout --confirmatory`:
    /// профиль вне списка — отказ; профиль из списка — проходит; без файла —
    /// отказ (заморозка обязана предшествовать).
    #[test]
    fn require_shortlist_member_gates_by_frozen_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("shortlist-frozen.txt");
        assert!(require_shortlist_member(&path, "marginal:side=bid").is_err());
        std::fs::write(
            &path,
            "# freeze_commit: abc\nmarginal:side=bid\ncross:SOLUSDT|pulled|[0,1)\n",
        )
        .unwrap();
        assert!(require_shortlist_member(&path, "marginal:side=bid").is_ok());
        assert!(require_shortlist_member(&path, "marginal:side=ask").is_err());
    }

    /// Граница модулей: чистая логика этого файла не тянет запрещённое —
    /// грепом по собственному исходнику (тот же приём, что `shortlist.rs`).
    #[test]
    fn module_avoids_forbidden_hot_path_primitives_outside_cli_glue() {
        const SRC: &str = include_str!("shortlist.rs");
        let banned = concat!("Inst", "ant::now");
        assert!(
            !SRC.contains(banned),
            "исходник тянет запрещённое: {banned}"
        );
    }
}
