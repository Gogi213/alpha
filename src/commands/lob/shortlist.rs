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
    PreregisteredWindow, VerdictHeader,
};
use crate::stats;

use super::profiles::{
    format_window_line, resolve_fill_model, run_profiles_with_fill_model, ProfilesArgs,
};
use super::{ExecutionArgs, H3Args};
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
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд).
    #[command(flatten)]
    pub h3: H3Args,
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
    /// Тройка `BacktestFillModel`: `--median-rtt-ns`, `--p95-rtt-ns`,
    /// `--order-qty-e9` (общие с `profiles`, `resolve_fill_model`, таск 16):
    /// заданы все три — разведочная/подтверждающая гоняются с
    /// `BacktestFillModel` (`observed_sharpe`/`fill`/`net_fill` становятся
    /// числами, DSR в вердикте зачитывается); не задан ни один —
    /// `NoFillModel`, как раньше.
    #[command(flatten)]
    pub execution: ExecutionArgs,
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

/// Группирует подкаталоги сессий по суткам их датированных частей
/// (`super::session_days_in_dir`, таск 23): каталог с частями за D и D+1
/// стоит под обеими датами — до таска 23 он числился только за сутками
/// `started_utc` своего `session.json`, то есть за последней сессией.
/// Каталоги без `session.json` или без датированных частей молча
/// пропускаются — не сессия, не ошибка (тот же приём, что
/// `profiles.rs`/`watch.rs`).
fn group_by_day(dirs: &[PathBuf]) -> BTreeMap<String, Vec<PathBuf>> {
    let mut out: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
    for dir in dirs {
        for day in super::session_days_in_dir(dir) {
            out.entry(day).or_default().push(dir.clone());
        }
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
/// сессий, среди суток которых есть хоть одни из `wanted_days`. Каталог,
/// стоящий под несколькими сутками (таск 23), линкуется один раз; части
/// его других суток отсеет окно «сейчас» `lob profiles` внутри времянки —
/// оно фильтрует по суткам части, не каталога.
fn build_filtered_root(
    dest_root: &Path,
    instruments_csv_src: &Path,
    by_day: &BTreeMap<String, Vec<PathBuf>>,
    wanted_days: &[String],
) -> anyhow::Result<()> {
    link_or_copy(instruments_csv_src, &instruments_csv_path(dest_root))?;
    let mut linked: std::collections::BTreeSet<PathBuf> = std::collections::BTreeSet::new();
    for day in wanted_days {
        let Some(dirs) = by_day.get(day) else {
            continue;
        };
        for dir in dirs {
            if !linked.insert(dir.clone()) {
                continue;
            }
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
    /// `net` после издержек, без веса на исполнение — ячейка матрицы
    /// «испытания × периоды» (таск 29): единственная денежная колонка,
    /// которая есть и без модели исполнения.
    net_bps: String,
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
    net_bps: Option<f64>,
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
                net_bps: parse_measured_f64(&row.net_bps),
                net_fill: parse_measured_f64(&row.net_fill),
                net_fill_lower: parse_measured_f64(&row.net_fill_lower),
                observed_sharpe: parse_measured_f64(&row.observed_sharpe),
            },
        );
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Матрица «испытания × периоды» — вход PBO и CPCV (таск 29, R47).
// ---------------------------------------------------------------------------

/// Матрица, которой PBO и CPCV проверяют **процедуру отбора**, а не
/// выбранный профиль (R47, задача §6.4 п. 4): строка — испытание, то есть
/// профиль сетки, тот же id, что уходит строкой в `runs.csv`; столбец —
/// календарные сутки окна, тот же кластер, на котором считается `G`;
/// ячейка — `net_bps` профиля за эти сутки.
///
/// **Пустая ячейка — `NaN`, не ноль.** Суток, за которые профиль не дал ни
/// одного наблюдения (`n = 0`), не бывает «нулевого net»: ноль здесь был бы
/// измеренной безубыточностью, которой не было. Что с `NaN` делает каждая
/// процедура, названо в их doc и печатается в шапке отчёта: блок с `NaN`
/// даёт испытанию Sharpe ровно `0` в этом сплите PBO
/// (`final_metrics::pbo`), складка CPCV с `NaN` пропускается
/// (`final_metrics::cpcv_mean_oos_sharpe`).
struct TrialDayMatrix {
    /// Периоды: сутки окна, по возрастанию.
    days: Vec<String>,
    /// Испытания: id профилей сетки, в порядке `build_profile_grid`.
    ids: Vec<String>,
    /// `rows[i][j]` — `net_bps` испытания `ids[i]` за сутки `days[j]`.
    rows: Vec<Vec<f64>>,
}

impl TrialDayMatrix {
    /// Ряд испытания по суткам. Читает только тест на ориентацию матрицы:
    /// боевой путь подаёт оценщикам матрицу целиком.
    #[cfg(test)]
    fn row(&self, id: &str) -> Option<&[f64]> {
        let i = self.ids.iter().position(|x| x == id)?;
        self.rows.get(i).map(Vec::as_slice)
    }

    /// Матрица из испытаний **без единой пропущенной ячейки** и число
    /// снятых. `NaN` не нейтрален для оценщика: `final_metrics::pbo` даёт
    /// испытанию с нефинитным значением Sharpe ровно `0.0` (его doc), то
    /// есть профиль, у которого за какие-то сутки данных нет, встал бы в
    /// IS-выборе **выше любого убыточного** и мог бы этот выбор выиграть —
    /// PBO считался бы по проигравшим. Такое испытание снимается целиком и
    /// до расчёта, обеими процедурами разом, а число снятых печатается в
    /// шапке: выборка, по которой посчитано, названа, а не подразумевается.
    fn without_nan_rows(&self) -> (Self, usize) {
        let keep: Vec<usize> = (0..self.ids.len())
            .filter(|&i| {
                self.rows
                    .get(i)
                    .is_some_and(|r| r.iter().all(|x| x.is_finite()))
            })
            .collect();
        let excluded = self.ids.len().saturating_sub(keep.len());
        let ids = keep
            .iter()
            .filter_map(|&i| self.ids.get(i).cloned())
            .collect();
        let rows = keep
            .iter()
            .filter_map(|&i| self.rows.get(i).cloned())
            .collect();
        (
            Self {
                days: self.days.clone(),
                ids,
                rows,
            },
            excluded,
        )
    }

    /// Строка `pbo_matrix: …` шапки: форма матрицы, сколько испытаний снято
    /// по `NaN`, чем заполнена ячейка (критерий приёмки таска 29 — правило
    /// пустой ячейки названо и в doc, и в шапке). `rows` — испытания,
    /// дошедшие до оценщиков, то есть уже после снятия.
    fn header_line(&self, excluded_nan_rows: usize) -> String {
        format!(
            "pbo_matrix: rows={} days={} excluded_nan_rows={} cell=net_bps \
             (n=0 за сутки → NaN, не ноль; испытание с NaN снято целиком: \
             оценщик дал бы ему Sharpe 0 и место выше убыточного)",
            self.ids.len(),
            self.days.len(),
            excluded_nan_rows,
        )
    }
}

/// PBO по матрице с числом блоков сводного отчёта
/// (`final_metrics::REPORT_PBO_PARTITIONS` — оно же порог «сколько суток
/// нужно», потому что `pbo` отказывает при `periods < partitions`).
/// Второй элемент — причина отсутствия числа для шапки: `None` без причины
/// шапка печатает как `none`, и именно эту немую форму таск 29 снимает.
fn pbo_of_matrix(m: &TrialDayMatrix) -> (Option<f64>, Option<String>) {
    let partitions = final_metrics::REPORT_PBO_PARTITIONS;
    let (rows, days) = (m.ids.len(), m.days.len());
    if days < partitions {
        return (None, Some(format!("days={days} < {partitions}")));
    }
    if rows < 2 {
        return (None, Some(format!("rows={rows} < 2 после исключения NaN")));
    }
    match final_metrics::pbo(&m.rows, partitions) {
        Some(v) => (Some(v), None),
        None => (
            None,
            Some(format!("матрица {rows}×{days} не принята оценщиком")),
        ),
    }
}

/// Правило отбора, которое проверяет CPCV, — то же, которым шорт-лист
/// выбирает профиль вердикта: лучший по среднему `net` на предъявленных
/// сутках. Печатается в шапке рядом с числом, чтобы «процедура отбора» в
/// отчёте была названа, а не подразумевалась.
pub(crate) const CPCV_SELECTION_RULE: &str = "лучший по среднему net на IS-сутках";

/// Само правило: индекс лучшей строки матрицы по среднему `net` на
/// поданных периодах. `Confirmed` шорт-листа добавляет к тому же
/// сравнению пороги `n`/`G`, которых суточная матрица не несёт по
/// построению (в ней только `net`), — это единственное расхождение, и оно
/// сужает не выбор, а множество, из которого он делается.
pub(crate) fn select_best_mean_net(trials: &[Vec<f64>], is_periods: &[usize]) -> Option<usize> {
    let mut best: Option<(usize, f64)> = None;
    for (i, row) in trials.iter().enumerate() {
        let vals: Vec<f64> = is_periods
            .iter()
            .filter_map(|&j| row.get(j).copied())
            .filter(|x| x.is_finite())
            .collect();
        if vals.is_empty() {
            continue;
        }
        let mean = vals.iter().sum::<f64>() / vals.len() as f64;
        if best.is_none_or(|(_, b)| mean > b) {
            best = Some((i, mean));
        }
    }
    best.map(|(i, _)| i)
}

/// CPCV **процедуры отбора** по той же матрице (задача §6.4 п. 4, В-19:
/// «проверяют процедуру отбора, а не выбранный профиль»): в каждой складке
/// отбор делается заново на IS-сутках правилом `select_best_mean_net`, а
/// Sharpe считается по OOS-суткам выбранного **в этой** складке испытания
/// (`final_metrics::cpcv_selection_oos_sharpe`). Параметры складок —
/// `REPORT_CPCV_PARAMS`, там же выведено, почему складок две и почему
/// защитные зоны на суточном ряду нулевые.
fn cpcv_of_matrix(m: &TrialDayMatrix) -> (Option<f64>, Option<String>) {
    let params = final_metrics::REPORT_CPCV_PARAMS;
    let need = final_metrics::min_obs_for_cpcv(params);
    let (rows, days) = (m.ids.len(), m.days.len());
    if days < need {
        return (None, Some(format!("days={days} < {need}")));
    }
    if rows < 2 {
        return (None, Some(format!("rows={rows} < 2 после исключения NaN")));
    }
    match final_metrics::cpcv_selection_oos_sharpe(&m.rows, params, select_best_mean_net) {
        Some(v) => (Some(v), None),
        None => (
            None,
            Some(format!(
                "матрица {rows}×{days}: ни одна складка не посчиталась"
            )),
        ),
    }
}

/// Что матрица отдала шапке: два числа, две причины их отсутствия, имя
/// правила отбора (только когда CPCV — число) и строка формы.
struct MatrixMetrics {
    pbo: Option<f64>,
    pbo_na: Option<String>,
    cpcv: Option<f64>,
    cpcv_na: Option<String>,
    cpcv_selection: Option<String>,
    line: String,
}

/// Строит матрицу и считает по ней обе процедуры. Суток окна меньше двух —
/// матрица **не строится вовсе**: делить период надвое не на чем, а один
/// прогон профилей на сутки стоит реплея всего пула (см.
/// `build_trial_day_matrix`); шапка сразу получает названную причину.
fn matrix_metrics(
    args: &ShortlistArgs,
    instruments_csv: &Path,
    by_day: &BTreeMap<String, Vec<PathBuf>>,
    window_days: &[String],
    grid: &[String],
) -> anyhow::Result<MatrixMetrics> {
    let days = window_days.len();
    if days < 2 {
        let why = format!("суток окна {days} < 2 — матрица не строилась");
        return Ok(MatrixMetrics {
            pbo: None,
            pbo_na: Some(why.clone()),
            cpcv: None,
            cpcv_na: Some(why),
            cpcv_selection: None,
            line: format!("pbo_matrix: не строилась — суток окна {days} < 2 (cell=net_bps)"),
        });
    }
    let full = build_trial_day_matrix(args, instruments_csv, by_day, window_days, grid)?;
    let (scored, excluded) = full.without_nan_rows();
    let (pbo, pbo_na) = pbo_of_matrix(&scored);
    let (cpcv, cpcv_na) = cpcv_of_matrix(&scored);
    Ok(MatrixMetrics {
        pbo,
        pbo_na,
        cpcv,
        cpcv_na,
        cpcv_selection: cpcv.map(|_| CPCV_SELECTION_RULE.to_string()),
        line: scored.header_line(excluded),
    })
}

/// Строит матрицу: один прогон `run_profiles_over` на каждые сутки окна во
/// времянку. Дороже разведочной ровно на число суток — иначе не бывает:
/// `profiles.rs` (зона не этого таска) агрегирует по всему корню сразу, и
/// разрешение по суткам достаётся только повтором прогона на суточном
/// подмножестве, тем же приёмом, что уже делает джекнайф ниже. Журнал
/// испытаний — во времянку: пересчёт той же сетки по суткам не есть новые
/// испытания (Decision 27).
fn build_trial_day_matrix(
    args: &ShortlistArgs,
    instruments_csv: &Path,
    by_day: &BTreeMap<String, Vec<PathBuf>>,
    window_days: &[String],
    grid: &[String],
) -> anyhow::Result<TrialDayMatrix> {
    let mut days = Vec::new();
    let mut columns: Vec<BTreeMap<String, ProfileNums>> = Vec::new();
    for day in window_days {
        if !by_day.contains_key(day) {
            continue;
        }
        let scratch = ScratchRoot::new(&format!("day-{day}"))?;
        build_filtered_root(
            scratch.path(),
            instruments_csv,
            by_day,
            std::slice::from_ref(day),
        )?;
        let prereg =
            write_full_coverage_preregistration(scratch.path(), std::slice::from_ref(day))?;
        let runs_scratch = ScratchRoot::new(&format!("day-runs-{day}"))?;
        let csv_path = run_profiles_over(
            scratch.path().to_path_buf(),
            runs_scratch.path().join("runs.csv"),
            scratch.path().join("profiles-day.csv"),
            false,
            Some(prereg),
            args,
        )?;
        columns.push(read_profile_table(&csv_path)?);
        days.push(day.clone());
    }
    let rows = grid
        .iter()
        .map(|id| {
            columns
                .iter()
                .map(|c| {
                    c.get(id)
                        .filter(|p| p.n > 0)
                        .and_then(|p| p.net_bps)
                        .unwrap_or(f64::NAN)
                })
                .collect()
        })
        .collect();
    Ok(TrialDayMatrix {
        days,
        ids: grid.to_vec(),
        rows,
    })
}

/// Пишет во времянку окно «сейчас» (ticket 21), накрывающее ровно те сутки,
/// которые уже отобраны в `dest_root` (`build_filtered_root`): нижняя и
/// верхняя дата поданного списка. `run_profiles_with_fill_model` требует
/// `--preregistration` в боевом режиме (R57 — окно не назначается молча);
/// этот файл — не второе окно поверх уже решённого сплита, а его же
/// граница, переданная тем же файловым каналом, что `lob profiles` ждёт
/// напрямую: фильтрация уже произошла через содержимое каталога-времянки, а
/// этот файл лишь не даёт внутреннему вызову отказать по отсутствию окна.
fn write_full_coverage_preregistration(
    dest_root: &Path,
    days: &[String],
) -> anyhow::Result<PathBuf> {
    let path = dest_root.join("window-preregistration.md");
    let first = days.iter().min().cloned().unwrap_or_default();
    let last = days.iter().max().cloned().unwrap_or_default();
    std::fs::write(
        &path,
        format!("exploratory: {first}\nconfirmatory: {last}\n"),
    )?;
    Ok(path)
}

fn run_profiles_over(
    root: PathBuf,
    runs_out: PathBuf,
    out: PathBuf,
    allow_unverified: bool,
    preregistration: Option<PathBuf>,
    base: &ShortlistArgs,
) -> anyhow::Result<PathBuf> {
    let profiles_args = ProfilesArgs {
        root,
        candidates_csv: base.candidates_csv.clone(),
        h3: base.h3,
        warmup_ms: base.warmup_ms,
        repeat_window_ms: base.repeat_window_ms,
        allow_unverified,
        out: Some(out),
        now_utc: base.now_utc.clone(),
        runs_out,
        execution: base.execution,
        preregistration,
        window_end: None,
    };
    // Та же тройка RTT/лота, та же модель — на разведочной, подтверждающей и
    // отладке (таск 16, `resolve_fill_model` — общая точка с `lob profiles`).
    let model = resolve_fill_model(
        base.execution.median_rtt_ns,
        base.execution.p95_rtt_ns,
        base.execution.order_qty_e9,
    )?;
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
    text.push_str("window: debug (all sessions)\n");
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
        None,
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
    let date = now
        .get(..10)
        .ok_or_else(|| anyhow::anyhow!("--now-utc некорректен: '{now}' короче 10 символов, ожидались первые 10 как YYYY-MM-DD"))?
        .to_string();

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
    let expl_preregistration =
        write_full_coverage_preregistration(expl_scratch.path(), &split.exploratory)?;
    let expl_csv = run_profiles_over(
        expl_scratch.path().to_path_buf(),
        args.runs_out.clone(),
        expl_scratch.path().join("profiles-exploratory.csv"),
        false,
        Some(expl_preregistration),
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
    let conf_preregistration =
        write_full_coverage_preregistration(conf_scratch.path(), &split.confirmatory)?;
    let conf_csv = run_profiles_over(
        conf_scratch.path().to_path_buf(),
        conf_runs_scratch.path().join("runs.csv"),
        conf_runs_scratch.path().join("profiles-confirmatory.csv"),
        false,
        Some(conf_preregistration),
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
    // выбранный профиль») — таск 29 снял заглушки `None` тасков 13/16.
    // Матрица «испытания × периоды» (`build_trial_day_matrix`): строка —
    // профиль сетки, столбец — сутки окна, ячейка — `net` за сутки. PBO
    // идёт по всей матрице (проверяется отбор), CPCV — по ряду выбранного
    // профиля (`cpcv_series_profile`). Число блоков и параметры CPCV — из
    // `final_metrics`, не отсюда; порога PBO ни задача, ни план не
    // назначают, поэтому число печатается без вердикта (сказано в шапке).
    let matrix_days: Vec<String> = split
        .exploratory
        .iter()
        .chain(split.confirmatory.iter())
        .cloned()
        .collect::<std::collections::BTreeSet<String>>()
        .into_iter()
        .collect();
    let matrix = matrix_metrics(args, &instruments_csv, &by_day, &matrix_days, &grid)?;

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
            let loo_preregistration =
                write_full_coverage_preregistration(loo_scratch.path(), &subset_days)?;
            let loo_csv = run_profiles_over(
                loo_scratch.path().to_path_buf(),
                loo_runs_scratch.path().join("runs.csv"),
                loo_runs_scratch.path().join("profiles-loo.csv"),
                false,
                Some(loo_preregistration),
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
    // Окно «сейчас» (ticket 21, R57) — то же самое, выведенное из уже
    // решённой границы `split` (файл предрегистрации), не новое число:
    // границы — крайние даты `exploratory`/`confirmatory`, сессии
    // внутри/вне — по тем же суткам `by_day`, что уже строили времянки выше.
    let window_bounds = PreregisteredWindow {
        start: split.exploratory.first().cloned().unwrap_or_default(),
        end: split.confirmatory.last().cloned().unwrap_or_default(),
        exploratory: split.exploratory.clone(),
        confirmatory: split.confirmatory.clone(),
    };
    let window_days: std::collections::BTreeSet<&String> = split
        .exploratory
        .iter()
        .chain(split.confirmatory.iter())
        .collect();
    let sessions_in_window: usize = by_day
        .iter()
        .filter(|(d, _)| window_days.contains(d))
        .map(|(_, v)| v.len())
        .sum();
    let total_valid_sessions: usize = by_day.values().map(Vec::len).sum();
    let sessions_outside_window = total_valid_sessions.saturating_sub(sessions_in_window);
    let window_line = format_window_line(
        &Some(window_bounds),
        sessions_in_window,
        sessions_outside_window,
    )?;
    let header = VerdictHeader {
        value_bps: best_confirmed_net_fill(&rows),
        dsr,
        pbo: matrix.pbo,
        cpcv_oos_sharpe: matrix.cpcv,
        pbo_na: matrix.pbo_na,
        cpcv_na: matrix.cpcv_na,
        cpcv_selection: matrix.cpcv_selection,
        pbo_matrix: matrix.line,
        g: g_for_header,
        p_grid_resolution: g_for_header.map(|g| stats::webb_p_grid_resolution(g as u32)),
        jackknife,
        window: window_line,
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
mod tests;
