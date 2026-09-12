//! `lob dashboard` — страница о монетах и их плотностях на localhost (таск 33,
//! R88: «я хотел дашборд о бизнес задаче конкретно — о монетах и их
//! плотностях»; заменяет таск 32, чей дашборд был «обо всём и ни о чём»).
//!
//! Команда **только читает**: живой каталог коллектора (`--root`) не
//! изменяется ни одним байтом, всё пишется в `--out` (`index.html` +
//! `data.json`, оба — через временный файл и переименование, чтобы страница,
//! перечитывающая `data.json` раз в 30 с, не поймала полуфайл).
//!
//! Что на странице — ровно предмет задачи (`BUSINESS-TASK.md`, логлайн):
//! крупная лимитная заявка (плотность) появилась, прожила, исчезла —
//! проели или сняли, — и куда после этого ушла цена. По каждой монете пула:
//!
//! 1. **плотности сейчас** — живые крупные уровни на последнем кадре записи
//!    (сторона, цена, расстояние до середины в bps, размер в лотах / в
//!    долларах / кратностью порога, возраст, который раз на этой цене);
//! 2. **картина за последний час** — середина цены и каждая плотность
//!    полоской от рождения до смерти на своей цене, цвет — исход;
//! 3. **что с ними стало** — по исходам: сколько, доля, время жизни, markout
//!    на четырёх горизонтах, `net` после издержек;
//! 4. **где стоят и как живут** — те же числа по осям профиля (сторона,
//!    размер, расстояние, время жизни, повторяемость) — маргиналы сетки
//!    `lob profiles` для этой монеты;
//! 5. **касания: цена дошла до плотности** (таск 36, R90, В-42/В-43/В-44) —
//!    касания живых уровней из `ReplayDay.touches` того же реплея: итог по
//!    исходам (отскочила / проели на касании) с markout «в сторону отскока»
//!    на четырёх горизонтах, маргиналы по осям В-44 (`lob::touch_axes`),
//!    крест исход × возраст, метки касаний на картине часа.
//!
//! Разметка уровней **не переписана**: `replay_symbol` — тот же реплей, что
//! у `lob levels`/`markout`/`watch`/`pilot`/`touches`; `markouts_for_level`,
//! `markouts_for_touch`, `approaches_for_touch` и `costs::observation_at` —
//! те же функции, что считают артефакты; корзины осей — `shortlist::*_LABELS`
//! и границы из `profiles`, корзины касаний — `lob::touch_axes`. Число на
//! странице получено тем же кодом, что число в CSV; второго прохода по
//! бинлогу ради касаний нет.
//!
//! Ни одного порога эта команда не изобретает: издержки — `ROUNDTRIP_FEES_BPS`,
//! горизонты — `HORIZONS_MS`, порог `H3` — `instruments.csv` каталога (и это
//! печатается: чей порог, какой `k`), RTT — В-37 (`assumed`), окно картины —
//! назначено здесь (`PICTURE_MINUTES`) и напечатано.

use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Args;

use crate::book::Side;
use crate::commands::record::FRAME_LOSS_WINDOW_SECS;
use crate::lob::costs::{mean_net_bps, observation_at, Observation, ROUNDTRIP_FEES_BPS};
use crate::lob::levels::{H3Mode, LevelsConfig, LiveLevel, Outcome, TouchRecord};
use crate::lob::markout::{
    approaches_for_touch, markouts_for_level, markouts_for_touch_outside, mid_double_tick,
    raw_return_bps, within_touch, MidSample, APPROACH_MS, HORIZONS_MS,
};
use crate::lob::shortlist::{
    repeat_bucket, DISTANCE_LABELS, DISTANCE_MAX_BPS, LIFETIME_LABELS, REPEAT_LABELS, SIDE_LABELS,
    SIZE_LABELS,
};
use crate::lob::touch_axes::{
    age_bucket, approach_bucket, frontrun_bucket, frontrun_share, round_bucket, touch_index_bucket,
    touch_outcome, AGE_LABELS, APPROACH_BOUNDS_BPS, APPROACH_LABELS, FRONTRUN_HALF,
    FRONTRUN_LABELS, ROUND_LABELS, TOUCH_INDEX_LABELS, TOUCH_OUTCOME_LABELS,
};
use crate::stats::{count_f64, count_f64_u64, G_MIN};

use super::profiles::{distance_bps_at_birth, distance_bucket, lifetime_bucket, size_bucket};
use super::session::SessionSummary;
use super::{
    median_trade_lots_for_symbol, replay_symbol, resolve_h3_mode_with_k, H3ModeArg,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::commands::record::instruments_csv_path;

// ---------------------------------------------------------------------------
// Назначенные числа. Каждое — с источником.
// ---------------------------------------------------------------------------

/// В-37: задержка круга принята владельцем за 20 мс до замера; всякий
/// артефакт с этим числом помечается `assumed`, не `measured`. В `net` на
/// странице RTT не входит (издержки — комиссии и спред, `costs::net_bps`);
/// печатается, чтобы читатель знал, какое число ждёт бэктест.
const ASSUMED_RTT_MS: u32 = 20;

/// Окно «картины»: последний час записи. Назначено здесь и напечатано на
/// странице; это окно показа, не окно вердикта (В-31 — окно «сейчас»
/// артефактов вердикта, В-33 — зачётный хвост пилота; ни то ни другое эта
/// страница не подменяет).
const PICTURE_MINUTES: i64 = 60;

/// Шаг прореживания середины на картине: одна точка в секунду (последний
/// срез секунды). Чистое прореживание для рисунка, на числа не влияет.
const PICTURE_MID_STEP_MS: i64 = 1_000;

/// Потолок полосок на картине: при отладочном пороге (`h3_lots = 1`) в час
/// рождаются десятки тысяч уровней, и браузер на таком SVG встаёт. Остаются
/// самые крупные (по максимуму размера); сколько было всего — печатается.
/// Тот же потолок — у меток касаний (таск 36): остаются самые крупные по
/// размеру уровня на касании (`size_at_touch`).
const PICTURE_MAX_BARS: usize = 4_000;

/// Как часто страница сама перечитывает `data.json` (секунды).
const PAGE_REFRESH_SECS: u64 = 30;

/// Индекс горизонта 10 с в `HORIZONS_MS` — тот же, что берёт `lob pilot`.
const H10S: usize = 2;

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Args)]
pub struct DashboardArgs {
    /// Каталог коллектора (олвейс-он или любой сессии): `session.json`,
    /// `<SYMBOL>-<день>[-pN].binlog`, `instruments.csv`. Только чтение.
    #[arg(long)]
    pub root: PathBuf,

    /// Куда положить `index.html` и `data.json`. Каталог создаётся.
    #[arg(long)]
    pub out: PathBuf,

    /// Режим порога `H3`. В отличие от `levels`/`markout`/`profiles` здесь
    /// есть умолчание `floor`, и это не отступление от правила «умолчания
    /// нет»: дашборд ничего не решает и не пишет в артефакты вердикта, а
    /// `percentile` требует часа прогрева и на записи короче часа даёт
    /// `levels = 0` — страница на тридцатой минуте была бы пуста.
    #[arg(long, value_enum, default_value = "floor")]
    pub h3_mode: H3ModeArg,

    /// Порог в лотах для `--h3-mode percentile` (у `floor` берётся из
    /// `instruments.csv` в `--root`, как у всех остальных подкоманд).
    #[arg(long)]
    pub h3_lots: Option<i64>,

    /// Относительный порог `floor(k × median_trade_lots)` (В-30) — тот же
    /// флаг, что у `lob levels --h3-k`; без него порог — колонка `h3_lots`.
    #[arg(long)]
    pub h3_k: Option<f64>,

    /// Пересчитывать и переписывать оба файла раз в N секунд, пока команду
    /// не остановят. Без флага — один расчёт и выход.
    #[arg(long)]
    pub watch: Option<u64>,
}

/// Что вернул один расчёт — для `dispatch` и для тестов.
#[derive(Debug, Clone)]
pub struct DashboardSummary {
    pub html: PathBuf,
    pub json: PathBuf,
    pub coins: usize,
    pub alive: Option<bool>,
    pub alive_reason: String,
}

// ---------------------------------------------------------------------------
// Данные страницы (`data.json`)
// ---------------------------------------------------------------------------

/// Одна строка про коллектор — жив ли и сколько записано. Пороги здесь не
/// выносятся (это страница о плотностях, не о процессе); разрывы — счётчик
/// живого `gaps.csv`, не часового снимка `session.json`.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct CollectorLine {
    pub alive: Option<bool>,
    pub alive_reason: String,
    pub started_utc: String,
    pub closed: bool,
    pub always_on: bool,
    pub recorded_secs: i64,
    pub instruments: usize,
    pub bytes_on_disk: u64,
    pub bytes_growth: Option<i64>,
    pub growth_window_secs: Option<i64>,
    /// Строк в `gaps.csv` без заголовка — разрывы за всю запись, живьём.
    pub gaps_on_disk: u64,
}

/// Живой крупный уровень на последнем кадре.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct LevelNow {
    pub side: String,
    pub price: f64,
    pub distance_bps: Option<f64>,
    pub size_lots: i64,
    /// Размер в единицах монеты (лоты × шаг лота).
    pub size_qty: f64,
    pub size_x_h3: f64,
    pub size_usd: f64,
    pub age_secs: i64,
    pub repeat_count: u32,
    pub traded_share: f64,
}

/// Одна строка «что стало»: по исходу или по корзине оси.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Row {
    pub label: String,
    pub n: usize,
    pub share: Option<f64>,
    pub share_eaten: Option<f64>,
    pub lifetime_median_ms: Option<f64>,
    /// Средний markout на четырёх горизонтах `HORIZONS_MS`, bps.
    pub m_bps: [Option<f64>; 4],
    /// Средний `net` на 10 с после издержек круга и спреда, bps.
    pub net_bps: Option<f64>,
    pub net_n: usize,
}

/// Полоска плотности на картине. Короткие имена — файл читает браузер,
/// полосок тысячи.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Bar {
    /// Рождение, мс.
    pub b: i64,
    /// Смерть, мс; `None` — уровень жив на последнем кадре.
    pub d: Option<i64>,
    /// Цена.
    pub p: f64,
    /// Сторона: `bid` / `ask`.
    pub s: String,
    /// Исход: `eaten` / `pulled` / `mixed` / `alive`.
    pub o: String,
    /// Размер (максимум за жизнь) кратностью порога `H3`.
    pub x: f64,
    /// Markout на 10 с, bps (нет у живых).
    pub m: Option<f64>,
}

/// Метка касания на картине (таск 36): треугольник у `start_ms` на цене
/// уровня, цвет — исход. Короткие имена, как у `Bar`.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Mark {
    /// Начало касания, мс.
    pub t: i64,
    /// Цена уровня.
    pub p: f64,
    /// Сторона: `bid` / `ask`.
    pub s: String,
    /// Исход касания: `bounced` / `eaten` (`touch_axes::TOUCH_OUTCOME_LABELS`).
    pub o: String,
    /// Размер уровня на касании кратностью порога `H3`.
    pub x: f64,
    /// Возраст уровня на касании, мс.
    pub a: i64,
    /// Длительность касания, мс.
    pub d: i64,
    /// Номер касания у уровня, с нуля.
    pub i: u32,
    /// Фронтран (за секунду до касания, В-45) в долях размера на касании.
    pub fr: Option<f64>,
    /// Сметено последним шагом цены (`swept_lots`, В-45) в долях размера.
    pub sw: Option<f64>,
    /// Подход за 1 с, bps (знак «к уровню»).
    pub ap: Option<f64>,
    /// Markout на 10 с «в сторону отскока», bps; `None` и при горизонте
    /// внутри касания (`w`).
    pub m: Option<f64>,
    /// Горизонт 10 с не длиннее касания — `m` ≈ 0 по построению (В-45),
    /// не печатается.
    pub w: bool,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Picture {
    pub from_ms: i64,
    pub to_ms: i64,
    /// Середина: `[метка мс, цена]`, одна точка в секунду.
    pub mid: Vec<[f64; 2]>,
    pub bars: Vec<Bar>,
    /// Сколько полосок вошло в окно и диапазон до потолка `PICTURE_MAX_BARS`.
    pub bars_total: usize,
    pub price_lo: f64,
    pub price_hi: f64,
    /// Сколько полосок не вошло в диапазон цены картины.
    pub clipped: usize,
    /// Метки касаний, начавшихся в окне (таск 36); потолок — тот же
    /// `PICTURE_MAX_BARS`, остаются самые крупные по размеру на касании.
    pub marks: Vec<Mark>,
    /// Сколько касаний вошло в окно и диапазон до потолка.
    pub marks_total: usize,
    /// Сколько касаний окна не вошло в диапазон цены картины.
    pub marks_clipped: usize,
}

/// Одна строка блока касаний: по исходу, по корзине оси или «все».
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TouchRow {
    pub label: String,
    pub n: usize,
    /// Доля строки среди всех касаний монеты.
    pub share: Option<f64>,
    /// Доля отскоков (`bounced`) в строке.
    pub share_bounced: Option<f64>,
    /// Средний markout «в сторону отскока» на `HORIZONS_MS` по всем
    /// касаниям строки, bps; горизонты внутри касания (`within_touch`,
    /// В-45) в среднее не входят.
    pub m_bps: [Option<f64>; 4],
    /// Сколько касаний строки дали markout на 10 с (`m_bps[H10S]`).
    pub m10s_n: usize,
    /// Средний markout на 10 с только по отскокам строки, bps.
    pub m10s_bounced_bps: Option<f64>,
    pub m10s_bounced_n: usize,
}

/// Клетка креста исход × возраст (В-44: один крест).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TouchCell {
    pub outcome: String,
    pub age: String,
    pub n: usize,
    /// Доля клетки среди всех касаний монеты.
    pub share: Option<f64>,
    pub m10s_bps: Option<f64>,
    pub m10s_n: usize,
}

/// Блок «Касания: цена дошла до плотности» (таск 36): касания из
/// `ReplayDay.touches`, корзины — `lob::touch_axes` (В-44), длительность и
/// размер — те же корзины, что у уровней (`LIFETIME_LABELS`/`SIZE_LABELS`).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Touches {
    pub total: usize,
    pub bounced: usize,
    pub eaten: usize,
    pub share_bounced: Option<f64>,
    pub per_hour: Option<f64>,
    /// Строки по исходам в порядке `TOUCH_OUTCOME_LABELS`.
    pub outcomes: Vec<TouchRow>,
    pub all: TouchRow,
    pub by_age: Vec<TouchRow>,
    pub by_frontrun: Vec<TouchRow>,
    pub by_round: Vec<TouchRow>,
    pub by_index: Vec<TouchRow>,
    pub by_approach: Vec<TouchRow>,
    pub by_duration: Vec<TouchRow>,
    pub by_side: Vec<TouchRow>,
    pub by_size: Vec<TouchRow>,
    /// Крест исход × возраст: `TOUCH_OUTCOME_LABELS` × `AGE_LABELS`, по
    /// строкам исхода.
    pub cross_outcome_age: Vec<TouchCell>,
    /// Касания без подхода за 1 с (нет среза за секунду до касания) — не
    /// вошли ни в одну корзину подхода.
    pub approach_missing: usize,
    /// Касания с размером на касании ниже порога `H3` (уровень к касанию
    /// усох, В-43: проверки размера при старте нет — фильтр в анализе) — не
    /// вошли ни в одну корзину размера.
    pub size_below_h3: usize,
    /// Окна подхода, мс (`APPROACH_MS`); на странице — первое.
    pub approach_ms: [i64; 2],
    /// Медиана «завала» — живых уровней ≥ `H3` той же стороны не дальше
    /// `DISTANCE_MAX_BPS` от уровня на касании (`stack_levels`, сам уровень
    /// входит; В-45). `None` при отладочном
    /// пороге (`instruments.csv` с меткой `# debug`): там почти весь топ-50 —
    /// уровни, число вырождено (В-43, свойство порога) — не показывается.
    pub stack_median: Option<f64>,
    pub stack_shown: bool,
    /// Окно «завала», bps (`DISTANCE_MAX_BPS`, В-45).
    pub stack_window_bps: i64,
    /// Порог — заглушка `k = 1.0` (В-30: `k` выбирает пилот, ещё не
    /// выбран): рядом с «завалом» страница печатает это словами.
    pub k_stub: bool,
    /// Сколько касаний на каждом горизонте `HORIZONS_MS` имели горизонт не
    /// длиннее касания (`markout::within_touch`, В-45) — их `m` в средние
    /// строк не вошли.
    pub within_touch: [usize; 4],
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Coin {
    pub symbol: String,
    pub error: Option<String>,
    pub tick_e9: i64,
    pub step_e9: i64,
    pub price_decimals: u32,
    pub h3_lots: i64,
    pub h3_source: String,
    pub last_ts_ms: Option<i64>,
    pub last_age_secs: Option<i64>,
    pub bid: Option<f64>,
    pub ask: Option<f64>,
    pub recorded_hours: f64,
    pub levels_total: usize,
    pub levels_per_hour: Option<f64>,
    pub levels_now: Vec<LevelNow>,
    pub outcomes: Vec<Row>,
    pub all: Row,
    pub by_side: Vec<Row>,
    pub by_size: Vec<Row>,
    pub by_distance: Vec<Row>,
    pub by_lifetime: Vec<Row>,
    pub by_repeat: Vec<Row>,
    pub picture: Picture,
    pub touches: Touches,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TextItem {
    pub title: String,
    pub body: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Dashboard {
    pub generated_utc: String,
    pub root: String,
    pub h3_mode: String,
    pub h3_k: Option<f64>,
    pub assumed_rtt_ms: u32,
    pub roundtrip_fees_bps: f64,
    pub horizons_ms: [i64; 4],
    pub tail_not_visible_secs: u64,
    pub picture_minutes: i64,
    pub g_min_days: usize,
    pub days_recorded: usize,
    pub collector: CollectorLine,
    pub coins: Vec<Coin>,
    pub glossary: Vec<TextItem>,
}

// ---------------------------------------------------------------------------
// Расчёт
// ---------------------------------------------------------------------------

/// Один расчёт или бесконечный цикл с шагом `--watch`. В режиме `--watch`
/// ошибка одного расчёта печатается и цикл живёт дальше (каталог пишется
/// прямо сейчас — кадр, пойманный на середине записи, на следующем проходе
/// уже целый); Ctrl+C — тот же способ остановки, что у коллектора.
pub fn run_dashboard(args: &DashboardArgs) -> anyhow::Result<DashboardSummary> {
    if let Some(secs) = args.watch {
        anyhow::ensure!(secs > 0, "--watch <секунды> обязан быть положителен");
        loop {
            match render_once(args) {
                Ok(summary) => eprintln!(
                    "dashboard: {} монет, {} — следующий расчёт через {secs} с (Ctrl+C — остановить)",
                    summary.coins, summary.alive_reason
                ),
                Err(e) => eprintln!("dashboard: расчёт не прошёл ({e}) — повтор через {secs} с"),
            }
            std::thread::sleep(Duration::from_secs(secs));
        }
    }
    render_once(args)
}

/// Один проход: прочитать каталог, посчитать, переписать оба файла.
fn render_once(args: &DashboardArgs) -> anyhow::Result<DashboardSummary> {
    let data = build_dashboard(args)?;
    std::fs::create_dir_all(&args.out)
        .map_err(|e| anyhow::anyhow!("не создать {}: {e}", args.out.display()))?;
    let json_path = args.out.join("data.json");
    let html_path = args.out.join("index.html");
    let json = serde_json::to_string(&data)?;
    write_atomic(&json_path, json.as_bytes())?;
    write_atomic(&html_path, render_html(&json).as_bytes())?;
    Ok(DashboardSummary {
        html: html_path,
        json: json_path,
        coins: data.coins.len(),
        alive: data.collector.alive,
        alive_reason: data.collector.alive_reason,
    })
}

/// Временный файл и переименование — тот же приём, что у `session.json`
/// (`session.rs`): читатель живого каталога никогда не видит полуфайл.
fn write_atomic(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, bytes)
        .map_err(|e| anyhow::anyhow!("не записать {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| {
        anyhow::anyhow!(
            "не переименовать {} → {}: {e}",
            tmp.display(),
            path.display()
        )
    })
}

/// Читает каталог и собирает всю страницу как данные. Отдельно от записи
/// файлов — тесты проверяют числа, а не ввод-вывод. Инструменты идут по
/// одному: реплей и его срезы середины живут только пока считается эта
/// монета (суточная запись — миллионы срезов на инструмент).
pub fn build_dashboard(args: &DashboardArgs) -> anyhow::Result<Dashboard> {
    let session_path = args.root.join("session.json");
    let raw = std::fs::read_to_string(&session_path).map_err(|e| {
        anyhow::anyhow!(
            "{}: не прочитать — это не каталог записи ({e})",
            session_path.display()
        )
    })?;
    let summary: SessionSummary = serde_json::from_str(&raw)
        .map_err(|e| anyhow::anyhow!("{}: не разобрать ({e})", session_path.display()))?;

    let now = chrono::Utc::now();
    let now_ms = now.timestamp_millis();
    let previous = read_previous(&args.out.join("data.json"), &args.root);

    let bytes_on_disk = binlog_bytes_on_disk(&args.root);
    let (bytes_growth, growth_window_secs) = match previous.as_ref() {
        Some(prev) => (
            Some(
                i64::try_from(bytes_on_disk).unwrap_or(i64::MAX)
                    - i64::try_from(prev.collector.bytes_on_disk).unwrap_or(i64::MAX),
            ),
            rfc3339_ms(&prev.generated_utc).map(|p| (now_ms - p) / 1000),
        ),
        None => (None, None),
    };
    let (alive, alive_reason) = liveness(&summary, bytes_growth, growth_window_secs);

    let h3_debug_marker = instruments_csv_marker(&args.root);
    let mut coins: Vec<Coin> = Vec::with_capacity(summary.instruments.len());
    let mut days = std::collections::BTreeSet::new();
    for symbol in &summary.instruments {
        match build_coin(args, symbol, now_ms, h3_debug_marker.as_deref(), &mut days) {
            Ok(c) => coins.push(c),
            Err(e) => coins.push(empty_coin(symbol, Some(e.to_string()))),
        }
    }

    // Длина записи: у остановленной — из `session.json`; у живой — самый
    // поздний кадр любого инструмента минус старт (не стенные часы: убитый
    // без `closed` процесс не растил бы это число вечно).
    let started_ms = rfc3339_ms(&summary.started_utc);
    let last_ms = coins.iter().filter_map(|c| c.last_ts_ms).max();
    let recorded_secs = if summary.closed {
        i64::try_from(summary.duration_s).unwrap_or(i64::MAX)
    } else {
        match (started_ms, last_ms) {
            (Some(s), Some(l)) => (l - s).max(0) / 1000,
            _ => i64::try_from(summary.duration_s).unwrap_or(i64::MAX),
        }
    };

    Ok(Dashboard {
        generated_utc: now.to_rfc3339(),
        root: args.root.display().to_string(),
        h3_mode: match args.h3_mode {
            H3ModeArg::Floor => "floor".to_string(),
            H3ModeArg::Percentile => "percentile".to_string(),
        },
        h3_k: args.h3_k,
        assumed_rtt_ms: ASSUMED_RTT_MS,
        roundtrip_fees_bps: ROUNDTRIP_FEES_BPS,
        horizons_ms: HORIZONS_MS,
        tail_not_visible_secs: FRAME_LOSS_WINDOW_SECS,
        picture_minutes: PICTURE_MINUTES,
        g_min_days: G_MIN,
        days_recorded: days.len(),
        collector: CollectorLine {
            alive,
            alive_reason,
            started_utc: summary.started_utc.clone(),
            closed: summary.closed,
            always_on: summary.always_on,
            recorded_secs,
            instruments: summary.instruments.len(),
            bytes_on_disk,
            bytes_growth,
            growth_window_secs,
            gaps_on_disk: gaps_rows(&args.root.join("gaps.csv")),
        },
        coins,
        glossary: glossary(),
    })
}

/// Одна монета: реплей тем же кодом, что `lob levels`/`markout`, и всё, что
/// страница о ней говорит. Реплей отпускается на выходе.
fn build_coin(
    args: &DashboardArgs,
    symbol: &str,
    now_ms: i64,
    debug_marker: Option<&str>,
    days: &mut std::collections::BTreeSet<String>,
) -> anyhow::Result<Coin> {
    let h3 = resolve_h3_mode_with_k(&args.root, symbol, args.h3_mode, args.h3_lots, args.h3_k)?;
    let h3_lots = match h3 {
        H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => h3_lots,
    };
    let h3_source = h3_source_label(&args.root, symbol, h3, args.h3_k, debug_marker);
    let cfg = LevelsConfig {
        mode: h3,
        warmup_ms: DEFAULT_WARMUP_MS,
        repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
    };
    let stats = replay_symbol(&args.root, symbol, cfg)?;
    for d in &stats.days {
        days.insert(d.day.clone());
    }
    let tick_e9 = stats.tick_e9;
    let step_e9 = stats.step_e9;
    let px = |tick: i64| price_of(tick, tick_e9);

    let first_ms = stats
        .days
        .iter()
        .filter_map(|d| d.mids.first().map(|s| s.ts_ms))
        .min();
    let last = stats
        .days
        .iter()
        .filter_map(|d| d.mids.last().copied())
        .max_by_key(|s| s.ts_ms);
    let last_ts_ms = last.map(|s| s.ts_ms);
    let recorded_hours = match (first_ms, last_ts_ms) {
        (Some(f), Some(l)) => ms_f64((l - f).max(0)) / 3_600_000.0,
        _ => 0.0,
    };

    // Живые уровни — против последней середины.
    let mut levels_now: Vec<LevelNow> = Vec::with_capacity(stats.open.len());
    if let Some(l) = last {
        let mid2x = mid_double_tick(l.bid_tick, l.ask_tick);
        for lv in &stats.open {
            levels_now.push(level_now(lv, mid2x, l.ts_ms, h3_lots, tick_e9, step_e9));
        }
        // Самые крупные первыми: при отладочном пороге живых уровней —
        // вся видимая книга, и смотреть надо на размер, не на близость.
        levels_now.sort_by(|a, b| b.size_x_h3.total_cmp(&a.size_x_h3));
    }

    // Свёртки: по исходам, по всем, по осям профиля.
    let mut all = Acc::default();
    let mut by_outcome: Vec<(&str, Acc)> = [Outcome::Eaten, Outcome::Pulled, Outcome::Mixed]
        .iter()
        .map(|o| (outcome_name(*o), Acc::default()))
        .collect();
    let mut by_side = labelled(&SIDE_LABELS);
    let mut by_size = labelled(&SIZE_LABELS);
    let mut by_distance = labelled(&DISTANCE_LABELS);
    let mut by_lifetime = labelled(&LIFETIME_LABELS);
    let mut by_repeat = labelled(&REPEAT_LABELS);
    let mut levels_total = 0usize;

    for day in &stats.days {
        for rec in &day.records {
            levels_total += 1;
            let outcome = rec.outcome();
            let m = markouts_for_level(rec, &day.mids);
            let obs = observation_at(rec, &day.mids, HORIZONS_MS[H10S]);
            let sample = Sample {
                outcome,
                lifetime_ms: rec.lifetime_ms,
                m,
                obs,
            };
            all.push(&sample);
            push_labelled(&mut by_outcome, outcome_name(outcome), &sample);
            push_labelled(&mut by_side, side_name(rec.side), &sample);
            if let Some(l) = size_bucket(size_ratio(rec.size_max, h3_lots)) {
                push_labelled(&mut by_size, l, &sample);
            }
            if let Some(l) = distance_bps_at_birth(&day.mids, rec).and_then(distance_bucket) {
                push_labelled(&mut by_distance, l, &sample);
            }
            if let Some(l) = lifetime_bucket(rec.lifetime_ms) {
                push_labelled(&mut by_lifetime, l, &sample);
            }
            push_labelled(&mut by_repeat, repeat_bucket(rec.repeat_count), &sample);
        }
    }

    let picture = build_picture(&stats.days, &stats.open, h3_lots, &px);
    // Касания — из того же реплея (`ReplayDay.touches`), второго прохода нет.
    let k_stub = h3_k_is_stub(&args.root, symbol, h3, args.h3_k);
    let touches = build_touches(
        &stats.days,
        h3_lots,
        recorded_hours,
        debug_marker.is_none(),
        k_stub,
    );

    Ok(Coin {
        symbol: symbol.to_string(),
        error: None,
        tick_e9,
        step_e9,
        price_decimals: decimals_of_e9(tick_e9),
        h3_lots,
        h3_source,
        last_ts_ms,
        // Кадр, сброшенный во время этого расчёта, моложе `now` — не «минус».
        last_age_secs: last_ts_ms.map(|l| (now_ms - l).max(0) / 1000),
        bid: last.map(|s| px(s.bid_tick)),
        ask: last.map(|s| px(s.ask_tick)),
        recorded_hours,
        levels_total,
        levels_per_hour: if recorded_hours > 0.0 {
            Some(count_f64(levels_total) / recorded_hours)
        } else {
            None
        },
        levels_now,
        outcomes: rows(&by_outcome, levels_total),
        all: all.row("все", levels_total),
        by_side: rows(&by_side, levels_total),
        by_size: rows(&by_size, levels_total),
        by_distance: rows(&by_distance, levels_total),
        by_lifetime: rows(&by_lifetime, levels_total),
        by_repeat: rows(&by_repeat, levels_total),
        picture,
        touches,
    })
}

/// Монета, которую не удалось прочитать: страница показывает причину, не
/// молчит и не сдвигает числа соседей.
fn empty_coin(symbol: &str, error: Option<String>) -> Coin {
    Coin {
        symbol: symbol.to_string(),
        error,
        tick_e9: 0,
        step_e9: 0,
        price_decimals: 0,
        h3_lots: 0,
        h3_source: String::new(),
        last_ts_ms: None,
        last_age_secs: None,
        bid: None,
        ask: None,
        recorded_hours: 0.0,
        levels_total: 0,
        levels_per_hour: None,
        levels_now: Vec::new(),
        outcomes: Vec::new(),
        all: Acc::default().row("все", 0),
        by_side: Vec::new(),
        by_size: Vec::new(),
        by_distance: Vec::new(),
        by_lifetime: Vec::new(),
        by_repeat: Vec::new(),
        picture: empty_picture(),
        touches: build_touches(&[], 0, 0.0, true, false),
    }
}

fn empty_picture() -> Picture {
    Picture {
        from_ms: 0,
        to_ms: 0,
        mid: Vec::new(),
        bars: Vec::new(),
        bars_total: 0,
        price_lo: 0.0,
        price_hi: 0.0,
        clipped: 0,
        marks: Vec::new(),
        marks_total: 0,
        marks_clipped: 0,
    }
}

fn level_now(
    lv: &LiveLevel,
    mid2x: i64,
    now_ms: i64,
    h3_lots: i64,
    tick_e9: i64,
    step_e9: i64,
) -> LevelNow {
    let price = price_of(lv.price_tick, tick_e9);
    let qty = lots_of(lv.size_lots, step_e9);
    LevelNow {
        side: side_name(lv.side).to_string(),
        price,
        distance_bps: raw_return_bps(mid2x, lv.price_tick.saturating_mul(2)).map(f64::abs),
        size_lots: lv.size_lots,
        size_qty: qty,
        size_x_h3: size_ratio(lv.size_lots, h3_lots),
        size_usd: price * qty,
        age_secs: (now_ms - lv.birth_ms).max(0) / 1000,
        repeat_count: lv.repeat_count,
        traded_share: if lv.size_max > 0 {
            ms_f64(lv.traded_lots) / ms_f64(lv.size_max)
        } else {
            0.0
        },
    }
}

/// Одно наблюдение уровня для свёрток.
struct Sample {
    outcome: Outcome,
    lifetime_ms: i64,
    m: [Option<f64>; 4],
    obs: Option<Observation>,
}

/// Накопитель строки.
#[derive(Default)]
struct Acc {
    n: usize,
    eaten: usize,
    lifetimes: Vec<f64>,
    m: [Vec<f64>; 4],
    obs: Vec<Observation>,
}

impl Acc {
    fn push(&mut self, s: &Sample) {
        self.n += 1;
        if s.outcome == Outcome::Eaten {
            self.eaten += 1;
        }
        self.lifetimes.push(ms_f64(s.lifetime_ms));
        for (i, m) in s.m.iter().enumerate() {
            if let Some(v) = m {
                self.m[i].push(*v);
            }
        }
        if let Some(o) = s.obs {
            self.obs.push(o);
        }
    }

    fn row(&self, label: &str, total: usize) -> Row {
        Row {
            label: label.to_string(),
            n: self.n,
            share: if total > 0 {
                Some(count_f64(self.n) / count_f64(total))
            } else {
                None
            },
            share_eaten: if self.n > 0 {
                Some(count_f64(self.eaten) / count_f64(self.n))
            } else {
                None
            },
            lifetime_median_ms: median(&self.lifetimes),
            m_bps: std::array::from_fn(|i| mean(&self.m[i])),
            net_bps: mean_net_bps(&self.obs),
            net_n: self.obs.len(),
        }
    }
}

/// Накопители по меткам оси в порядке меток — один для уровней (`Acc`) и
/// для касаний (`TouchAcc`).
fn labelled<A: Default>(labels: &[&'static str]) -> Vec<(&'static str, A)> {
    labels.iter().map(|l| (*l, A::default())).collect()
}

fn find_labelled<'a, A>(accs: &'a mut [(&str, A)], label: &str) -> Option<&'a mut A> {
    accs.iter_mut()
        .find(|(l, _)| *l == label)
        .map(|(_, acc)| acc)
}

fn push_labelled(accs: &mut [(&str, Acc)], label: &str, s: &Sample) {
    if let Some(acc) = find_labelled(accs, label) {
        acc.push(s);
    }
}

fn rows(accs: &[(&str, Acc)], total: usize) -> Vec<Row> {
    accs.iter().map(|(l, a)| a.row(l, total)).collect()
}

// ---------------------------------------------------------------------------
// Касания (таск 36)
// ---------------------------------------------------------------------------

/// Одно касание для свёрток: исход и markout «в сторону отскока».
struct TouchSample {
    bounced: bool,
    m: [Option<f64>; 4],
}

/// Накопитель строки касаний.
#[derive(Default)]
struct TouchAcc {
    n: usize,
    bounced: usize,
    m: [Vec<f64>; 4],
    m10s_bounced: Vec<f64>,
}

impl TouchAcc {
    fn push(&mut self, s: &TouchSample) {
        self.n += 1;
        if s.bounced {
            self.bounced += 1;
        }
        for (i, m) in s.m.iter().enumerate() {
            if let Some(v) = m {
                self.m[i].push(*v);
            }
        }
        if s.bounced {
            if let Some(v) = s.m[H10S] {
                self.m10s_bounced.push(v);
            }
        }
    }

    fn row(&self, label: &str, total: usize) -> TouchRow {
        TouchRow {
            label: label.to_string(),
            n: self.n,
            share: share_of(self.n, total),
            share_bounced: share_of(self.bounced, self.n),
            m_bps: std::array::from_fn(|i| mean(&self.m[i])),
            m10s_n: self.m[H10S].len(),
            m10s_bounced_bps: mean(&self.m10s_bounced),
            m10s_bounced_n: self.m10s_bounced.len(),
        }
    }

    fn cell(&self, outcome: &str, age: &str, total: usize) -> TouchCell {
        TouchCell {
            outcome: outcome.to_string(),
            age: age.to_string(),
            n: self.n,
            share: share_of(self.n, total),
            m10s_bps: mean(&self.m[H10S]),
            m10s_n: self.m[H10S].len(),
        }
    }
}

fn share_of(part: usize, total: usize) -> Option<f64> {
    (total > 0).then(|| count_f64(part) / count_f64(total))
}

fn push_touch(accs: &mut [(&str, TouchAcc)], label: &str, s: &TouchSample) {
    if let Some(acc) = find_labelled(accs, label) {
        acc.push(s);
    }
}

fn touch_rows(accs: &[(&str, TouchAcc)], total: usize) -> Vec<TouchRow> {
    accs.iter().map(|(l, a)| a.row(l, total)).collect()
}

/// Блок касаний монеты из `ReplayDay.touches` суток реплея. Markout —
/// `markouts_for_touch` (база — срез как есть на `start_ms`, В-43), подход
/// — `approaches_for_touch`; корзины — `lob::touch_axes` (В-44), длительность
/// и размер — корзины уровней (`profiles::lifetime_bucket`/`size_bucket`).
/// Без касаний — блок с нулями и всеми метками, не ошибка.
fn build_touches(
    days: &[super::ReplayDay],
    h3_lots: i64,
    recorded_hours: f64,
    stack_shown: bool,
    k_stub: bool,
) -> Touches {
    let mut all = TouchAcc::default();
    let mut by_outcome: Vec<(&str, TouchAcc)> = labelled(&TOUCH_OUTCOME_LABELS);
    let mut by_age = labelled(&AGE_LABELS);
    let mut by_frontrun = labelled(&FRONTRUN_LABELS);
    let mut by_round = labelled(&ROUND_LABELS);
    let mut by_index = labelled(&TOUCH_INDEX_LABELS);
    let mut by_approach = labelled(&APPROACH_LABELS);
    let mut by_duration = labelled(&LIFETIME_LABELS);
    let mut by_side = labelled(&SIDE_LABELS);
    let mut by_size = labelled(&SIZE_LABELS);
    // Крест исход × возраст: клетки по строкам исхода, внутри — по возрасту.
    let mut cross: Vec<((&str, &str), TouchAcc)> = TOUCH_OUTCOME_LABELS
        .iter()
        .flat_map(|o| {
            AGE_LABELS
                .iter()
                .map(move |a| ((*o, *a), TouchAcc::default()))
        })
        .collect();
    let mut total = 0usize;
    let mut approach_missing = 0usize;
    let mut size_below_h3 = 0usize;
    let mut stacks: Vec<f64> = Vec::new();
    let mut within = [0usize; 4];

    for day in days {
        for t in &day.touches {
            total += 1;
            let outcome = touch_outcome(t.ended_by_death);
            for (c, inside) in within.iter_mut().zip(within_touch(t.duration_ms)) {
                *c += usize::from(inside);
            }
            // Горизонт внутри касания — `None` в средних (В-45 (2)).
            let sample = TouchSample {
                bounced: !t.ended_by_death,
                m: markouts_for_touch_outside(t, &day.mids),
            };
            all.push(&sample);
            push_touch(&mut by_outcome, outcome, &sample);
            let age = age_bucket(t.age_ms());
            if let Some(a) = age {
                push_touch(&mut by_age, a, &sample);
                if let Some((_, acc)) = cross
                    .iter_mut()
                    .find(|((o, l), _)| *o == outcome && *l == a)
                {
                    acc.push(&sample);
                }
            }
            if let Some(l) =
                frontrun_share(t.frontrun_lots, t.size_at_touch).and_then(frontrun_bucket)
            {
                push_touch(&mut by_frontrun, l, &sample);
            }
            push_touch(&mut by_round, round_bucket(t.round_zeros), &sample);
            push_touch(&mut by_index, touch_index_bucket(t.touch_index), &sample);
            match approaches_for_touch(t, &day.mids)[0].and_then(approach_bucket) {
                Some(l) => push_touch(&mut by_approach, l, &sample),
                None => approach_missing += 1,
            }
            if let Some(l) = lifetime_bucket(t.duration_ms) {
                push_touch(&mut by_duration, l, &sample);
            }
            push_touch(&mut by_side, side_name(t.side), &sample);
            match size_bucket(size_ratio(t.size_at_touch, h3_lots)) {
                Some(l) => push_touch(&mut by_size, l, &sample),
                None => size_below_h3 += 1,
            }
            stacks.push(ms_f64(i64::from(t.stack_levels)));
        }
    }

    let bounced = all.bounced;
    Touches {
        total,
        bounced,
        eaten: total - bounced,
        share_bounced: share_of(bounced, total),
        per_hour: (recorded_hours > 0.0).then(|| count_f64(total) / recorded_hours),
        outcomes: touch_rows(&by_outcome, total),
        all: all.row("все", total),
        by_age: touch_rows(&by_age, total),
        by_frontrun: touch_rows(&by_frontrun, total),
        by_round: touch_rows(&by_round, total),
        by_index: touch_rows(&by_index, total),
        by_approach: touch_rows(&by_approach, total),
        by_duration: touch_rows(&by_duration, total),
        by_side: touch_rows(&by_side, total),
        by_size: touch_rows(&by_size, total),
        cross_outcome_age: cross
            .iter()
            .map(|((o, a), acc)| acc.cell(o, a, total))
            .collect(),
        approach_missing,
        size_below_h3,
        approach_ms: APPROACH_MS,
        stack_median: if stack_shown { median(&stacks) } else { None },
        stack_shown,
        stack_window_bps: DISTANCE_MAX_BPS,
        k_stub,
        within_touch: within,
    }
}

/// Картина последнего часа: середина раз в секунду, полоски всех уровней,
/// умерших в окне (родились когда угодно), и живых. Диапазон цены — по
/// середине окна плюс самая дальняя корзина расстояния (`DISTANCE_LABELS`
/// кончается на 25 bps): уровни дальше не входят ни в одну корзину сетки и
/// на картине только сжали бы масштаб; их число печатается.
fn build_picture(
    days: &[super::ReplayDay],
    open: &[LiveLevel],
    h3_lots: i64,
    px: &dyn Fn(i64) -> f64,
) -> Picture {
    let Some(to_ms) = days
        .iter()
        .filter_map(|d| d.mids.last().map(|s| s.ts_ms))
        .max()
    else {
        return empty_picture();
    };
    let from_ms = to_ms - PICTURE_MINUTES * 60_000;

    let mut mid: Vec<[f64; 2]> = Vec::new();
    let mut lo = f64::INFINITY;
    let mut hi = f64::NEG_INFINITY;
    let mut bucket: Option<(i64, MidSample)> = None;
    for day in days {
        let start = day.mids.partition_point(|s| s.ts_ms < from_ms);
        for s in &day.mids[start..] {
            let sec = s.ts_ms.div_euclid(PICTURE_MID_STEP_MS);
            match bucket {
                Some((b, _)) if b == sec => bucket = Some((sec, *s)),
                Some((_, prev)) => {
                    push_mid(&mut mid, &mut lo, &mut hi, prev, px);
                    bucket = Some((sec, *s));
                }
                None => bucket = Some((sec, *s)),
            }
        }
    }
    if let Some((_, prev)) = bucket {
        push_mid(&mut mid, &mut lo, &mut hi, prev, px);
    }
    if !lo.is_finite() || !hi.is_finite() {
        lo = 0.0;
        hi = 0.0;
    }
    let max_bps = crate::lob::shortlist::DISTANCE_BOUNDS_BPS
        .last()
        .map_or(0.0, |(_, hi)| *hi);
    let price_lo = lo * (1.0 - max_bps / 10_000.0);
    let price_hi = hi * (1.0 + max_bps / 10_000.0);

    let mut bars: Vec<Bar> = Vec::new();
    let mut clipped = 0usize;
    for day in days {
        for rec in &day.records {
            if rec.death_ms < from_ms {
                continue;
            }
            let p = px(rec.price_tick);
            if p < price_lo || p > price_hi {
                clipped += 1;
                continue;
            }
            bars.push(Bar {
                b: rec.birth_ms,
                d: Some(rec.death_ms),
                p,
                s: side_name(rec.side).to_string(),
                o: outcome_name(rec.outcome()).to_string(),
                x: size_ratio(rec.size_max, h3_lots),
                m: markouts_for_level(rec, &day.mids)[H10S],
            });
        }
    }
    for lv in open {
        let p = px(lv.price_tick);
        if p < price_lo || p > price_hi {
            clipped += 1;
            continue;
        }
        bars.push(Bar {
            b: lv.birth_ms,
            d: None,
            p,
            s: side_name(lv.side).to_string(),
            o: "alive".to_string(),
            x: size_ratio(lv.size_max, h3_lots),
            m: None,
        });
    }
    let bars_total = bars.len();
    if bars.len() > PICTURE_MAX_BARS {
        bars.sort_by(|a, b| b.x.total_cmp(&a.x));
        bars.truncate(PICTURE_MAX_BARS);
    }

    // Метки касаний, начавшихся в окне (таск 36): те же диапазон цены и
    // потолок, что у полосок; крупные — по размеру уровня на касании.
    let mut marks: Vec<Mark> = Vec::new();
    let mut marks_clipped = 0usize;
    for day in days {
        for t in &day.touches {
            if t.start_ms < from_ms {
                continue;
            }
            let p = px(t.price_tick);
            if p < price_lo || p > price_hi {
                marks_clipped += 1;
                continue;
            }
            marks.push(mark_of(t, &day.mids, p, h3_lots));
        }
    }
    let marks_total = marks.len();
    if marks.len() > PICTURE_MAX_BARS {
        marks.sort_by(|a, b| b.x.total_cmp(&a.x));
        marks.truncate(PICTURE_MAX_BARS);
    }
    Picture {
        from_ms,
        to_ms,
        mid,
        bars,
        bars_total,
        price_lo,
        price_hi,
        clipped,
        marks,
        marks_total,
        marks_clipped,
    }
}

fn mark_of(t: &TouchRecord, mids: &[MidSample], price: f64, h3_lots: i64) -> Mark {
    Mark {
        t: t.start_ms,
        p: price,
        s: side_name(t.side).to_string(),
        o: touch_outcome(t.ended_by_death).to_string(),
        x: size_ratio(t.size_at_touch, h3_lots),
        a: t.age_ms(),
        d: t.duration_ms,
        i: t.touch_index,
        fr: frontrun_share(t.frontrun_lots, t.size_at_touch),
        sw: frontrun_share(t.swept_lots, t.size_at_touch),
        ap: approaches_for_touch(t, mids)[0],
        m: markouts_for_touch_outside(t, mids)[H10S],
        w: within_touch(t.duration_ms)[H10S],
    }
}

fn push_mid(
    mid: &mut Vec<[f64; 2]>,
    lo: &mut f64,
    hi: &mut f64,
    s: MidSample,
    px: &dyn Fn(i64) -> f64,
) {
    let p = (px(s.bid_tick) + px(s.ask_tick)) / 2.0;
    *lo = lo.min(p);
    *hi = hi.max(p);
    mid.push([ms_f64(s.ts_ms), p]);
}

// ---------------------------------------------------------------------------
// Коллектор: жив ли, сколько записано
// ---------------------------------------------------------------------------

/// Жив/нет — по росту суммы бинлогов между двумя расчётами; `closed` —
/// остановлен штатно. Первый расчёт честно отвечает «не знаю». Числа
/// порогов здесь нет: вырос — пишет, не вырос за окно — не пишет.
fn liveness(
    summary: &SessionSummary,
    bytes_growth: Option<i64>,
    growth_window_secs: Option<i64>,
) -> (Option<bool>, String) {
    if summary.closed {
        return (
            Some(false),
            format!(
                "остановлен штатно (session.json closed=true), записано {}",
                human_duration(i64::try_from(summary.duration_s).unwrap_or(i64::MAX))
            ),
        );
    }
    match (bytes_growth, growth_window_secs) {
        (Some(g), Some(w)) if g > 0 => (
            Some(true),
            format!(
                "пишет: бинлоги выросли на {} за {}",
                human_bytes(u64::try_from(g).unwrap_or(0)),
                human_duration(w)
            ),
        ),
        (Some(_), Some(w)) => (
            Some(false),
            format!("не пишет: бинлоги не выросли за {}", human_duration(w)),
        ),
        _ => (
            None,
            "первый расчёт — жив ли, узнаем по росту бинлогов на следующем".to_string(),
        ),
    }
}

fn binlog_bytes_on_disk(root: &Path) -> u64 {
    let Ok(entries) = std::fs::read_dir(root) else {
        return 0;
    };
    entries
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().ends_with(".binlog"))
        .filter_map(|e| e.metadata().ok())
        .map(|m| m.len())
        .sum()
}

/// Строк данных в `gaps.csv` (без заголовка). Нет файла — 0: коллектор
/// создаёт его на старте, отсутствие означает не «нет разрывов», а «не
/// каталог коллектора», и это уже сказал `session.json`.
fn gaps_rows(path: &Path) -> u64 {
    std::fs::read_to_string(path)
        .map(|s| {
            u64::try_from(
                s.lines()
                    .filter(|l| !l.trim().is_empty())
                    .count()
                    .saturating_sub(1),
            )
            .unwrap_or(0)
        })
        .unwrap_or(0)
}

/// Прежний `data.json` того же каталога — база для роста бинлогов. Чужой
/// `--root` в том же `--out` не считается: рост сравнивался бы с чужим числом.
fn read_previous(path: &Path, root: &Path) -> Option<Dashboard> {
    let raw = std::fs::read_to_string(path).ok()?;
    let prev: Dashboard = serde_json::from_str(&raw).ok()?;
    (prev.root == root.display().to_string()).then_some(prev)
}

/// Первая строка `instruments.csv`, если это метка `# debug: …` — порог
/// `H3` тогда отладочный, и страница обязана это сказать.
fn instruments_csv_marker(root: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(root.join("instruments.csv")).ok()?;
    let first = raw.lines().next()?.trim();
    first
        .starts_with('#')
        .then(|| first.trim_start_matches('#').trim().to_string())
}

/// Заглушка множителя пола `k` (В-30: `k` выбирает пилот по сетке
/// `pilot::K_GRID`, пока не выбран — `lob pick --h3-k 1.0`). Страница при
/// этом `k` печатает «k — заглушка» рядом с «завалом»: при `k = 1.0` почти
/// весь топ-50 — уровни, число завала — свойство порога, не рынка.
pub const K_STUB: f64 = 1.0;

/// Колонка `k` строки символа в `instruments.csv` (пишет `lob pick`); нет
/// файла, колонки или строки — `None`, страница тогда заглушку не
/// объявляет.
fn h3_k_for_symbol(instruments_csv: &Path, symbol: &str) -> Option<f64> {
    let mut r = crate::commands::lob::pick::instruments_csv_reader(instruments_csv).ok()?;
    let headers = r.headers().ok()?.clone();
    let k_col = headers.iter().position(|h| h == "k")?;
    let sym_col = headers.iter().position(|h| h == "symbol")?;
    for row in r.records() {
        let row = row.ok()?;
        if row.get(sym_col) == Some(symbol) {
            return row.get(k_col)?.trim().parse().ok();
        }
    }
    None
}

/// Действующий `k` монеты — `--h3-k`, иначе колонка `k` файла — равен
/// заглушке `K_STUB`; только в режиме `floor` (у `percentile` `k` нет).
fn h3_k_is_stub(root: &Path, symbol: &str, h3: H3Mode, h3_k: Option<f64>) -> bool {
    match h3 {
        H3Mode::Floor { .. } => h3_k
            .or_else(|| h3_k_for_symbol(&instruments_csv_path(root), symbol))
            .is_some_and(|k| k == K_STUB),
        H3Mode::Percentile { .. } => false,
    }
}

/// Подпись порога: откуда число и какой `k` — чтобы «крупный уровень» на
/// странице не был словом без определения.
fn h3_source_label(
    root: &Path,
    symbol: &str,
    h3: H3Mode,
    h3_k: Option<f64>,
    debug_marker: Option<&str>,
) -> String {
    let median = median_trade_lots_for_symbol(&instruments_csv_path(root), symbol).ok();
    let mut s = match h3 {
        H3Mode::Floor { h3_lots } => match (h3_k, median) {
            (Some(k), Some(m)) => {
                format!("порог {h3_lots} лотов = floor({k} × медианная сделка {m} лотов), --h3-k")
            }
            (None, Some(m)) => format!(
                "порог {h3_lots} лотов — колонка h3_lots в instruments.csv (медианная сделка {m} лотов)"
            ),
            _ => format!("порог {h3_lots} лотов — колонка h3_lots в instruments.csv"),
        },
        H3Mode::Percentile { h3_lots } => format!("порог {h3_lots} лотов — 99-й перцентиль, --h3-lots"),
    };
    if let Some(m) = debug_marker {
        s.push_str(&format!("; instruments.csv помечен: {m}"));
    }
    s
}

// ---------------------------------------------------------------------------
// Мелочи
// ---------------------------------------------------------------------------

/// `i64` → `f64` для миллисекунд, лотов и прочих счётчиков вне горячего
/// пути: единственная точка каста в модуле.
#[allow(clippy::cast_precision_loss)]
fn ms_f64(v: i64) -> f64 {
    v as f64
}

/// Цена в единицах котировки: тик × шаг тика. Только для показа — везде до
/// этой строки цена целая (A1).
fn price_of(tick: i64, tick_e9: i64) -> f64 {
    ms_f64(tick) * ms_f64(tick_e9) / 1e9
}

fn lots_of(lots: i64, step_e9: i64) -> f64 {
    ms_f64(lots) * ms_f64(step_e9) / 1e9
}

fn size_ratio(lots: i64, h3_lots: i64) -> f64 {
    if h3_lots > 0 {
        ms_f64(lots) / ms_f64(h3_lots)
    } else {
        0.0
    }
}

/// Сколько знаков после запятой нужно цене с таким шагом тика.
fn decimals_of_e9(tick_e9: i64) -> u32 {
    if tick_e9 <= 0 {
        return 0;
    }
    let mut zeros = 0u32;
    let mut v = tick_e9;
    while v % 10 == 0 && zeros < 9 {
        v /= 10;
        zeros += 1;
    }
    9 - zeros
}

fn side_name(side: Side) -> &'static str {
    match side {
        Side::Bid => SIDE_LABELS[0],
        Side::Ask => SIDE_LABELS[1],
    }
}

fn outcome_name(o: Outcome) -> &'static str {
    match o {
        Outcome::Eaten => "eaten",
        Outcome::Pulled => "pulled",
        Outcome::Mixed => "mixed",
    }
}

fn rfc3339_ms(s: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|t| t.timestamp_millis())
}

fn median(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut v = values.to_vec();
    v.sort_by(f64::total_cmp);
    let n = v.len();
    Some(if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    })
}

fn mean(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    Some(values.iter().sum::<f64>() / count_f64(values.len()))
}

fn human_bytes(bytes: u64) -> String {
    let b = count_f64_u64(bytes);
    if b >= 1e9 {
        format!("{:.2} ГБ", b / 1e9)
    } else if b >= 1e6 {
        format!("{:.1} МБ", b / 1e6)
    } else if b >= 1e3 {
        format!("{:.0} КБ", b / 1e3)
    } else {
        format!("{bytes} Б")
    }
}

fn human_duration(secs: i64) -> String {
    if secs < 60 {
        format!("{secs} с")
    } else if secs < 3600 {
        format!("{} мин", secs / 60)
    } else {
        format!("{} ч {} мин", secs / 3600, (secs % 3600) / 60)
    }
}

fn item(title: &str, body: &str) -> TextItem {
    TextItem {
        title: title.to_string(),
        body: body.to_string(),
    }
}

/// Словарь — одно предложение на термин, простыми словами. Числа в нём —
/// те же константы кода, не переписанные руками.
fn glossary() -> Vec<TextItem> {
    vec![
        item(
            "Плотность (крупный уровень)",
            "лимитная заявка в стакане, размер которой больше порога H3 (порог — в лотах, \
             из instruments.csv записи; чей он — написано у каждой монеты). Уровень «родился», когда \
             размер на цене превысил порог, и «умер», когда упал ниже 20 % своего максимума \
             или цена вышла из первых 50 уровней книги.",
        ),
        item(
            "Проели / сняли / смешанно (eaten / pulled / mixed)",
            "как уровень умер: проели — сделки против него съели не меньше 70 % максимума \
             (был настоящий агрессор); сняли — сделок было меньше 20 % (владелец убрал заявку); \
             смешанно — между.",
        ),
        item(
            "Markout (m)",
            &format!(
                "насколько сдвинулась середина цены через {} мс / {} с / {} с / {} с после \
                 смерти уровня, в базисных пунктах (1 bps = 0.01 %), со знаком «в сторону \
                 плотности»: плюс — цена ушла туда, куда «должна» после такой плотности.",
                HORIZONS_MS[0],
                HORIZONS_MS[1] / 1000,
                HORIZONS_MS[2] / 1000,
                HORIZONS_MS[3] / 1000
            ),
        ),
        item(
            "net",
            &format!(
                "markout на 10 с минус издержки круга: комиссии {ROUNDTRIP_FEES_BPS} bps \
                 (мейкер + тейкер) и проскальзывание по спреду в момент выхода. Больше нуля — \
                 движение окупает издержки. Это ещё не деньги: доля исполнившихся входов (fill) \
                 считается бэктестом, не здесь."
            ),
        ),
        item(
            "Расстояние",
            "от цены уровня до середины стакана в момент рождения, в bps — не в тиках: тик \
             на разных монетах стоит по-разному.",
        ),
        item(
            "Размер ×H3",
            "во сколько раз максимум уровня больше порога H3.",
        ),
        item(
            "Который раз на цене",
            "сколько уровней уже рождалось на этой цене и стороне за последний час.",
        ),
        item(
            "Касание",
            "цена дошла до живой плотности: уровень, живший до этого кадра, стал лучшей \
             ценой своей стороны (первым в стакане). Касание кончается, когда уровень \
             перестал быть лучшей ценой или умер. Уровень, родившийся сразу лучшей ценой, \
             касанием не считается (В-43). Один уровень могут коснуться несколько раз — \
             «номер касания» это и считает.",
        ),
        item(
            "Отскок / проели на касании",
            "исход касания: отскок — уровень остался жив, а цена от него ушла; проели на \
             касании — касание кончилось смертью уровня. Markout касания берётся от середины \
             в момент касания (В-43) со знаком «в сторону отскока»: плюс — цена ушла от \
             плотности. Это движение, не сделка: вход/стоп/тейк отскока считает бэктест, не \
             эта страница.",
        ),
        item(
            "Фронтран",
            &format!(
                "заявки той же стороны, стоящие ближе к середине, чем плотность (перед ней), \
                 за секунду до касания (кадр в 1–2 с до него, В-45) — в долях её размера на \
                 касании. На последнем кадре до касания перед плотностью всегда стоит прежняя \
                 лучшая цена — то, что смёл последний шаг («сметено»), показывается в наведении \
                 на метку касания отдельно. Практики: «плотность на лям и фронтрана на 500 \
                 тысяч» — отсюда граница {FRONTRUN_HALF} (В-44)."
            ),
        ),
        item(
            "Подход",
            &format!(
                "как двигалась середина за {} с до касания, в bps, со знаком «к уровню»: \
                 плюс — цена шла на плотность (импульсный подход), минус — уходила от неё. \
                 Корзины — первые границы расстояния ({}, {}, {} bps) и отдельная «<0» (В-44).",
                APPROACH_MS[0] / 1000,
                APPROACH_BOUNDS_BPS[1].0,
                APPROACH_BOUNDS_BPS[2].0,
                APPROACH_BOUNDS_BPS[3].0
            ),
        ),
        item(
            "Круглость",
            "сколько нулей в конце цены в тиках (0 / 1 / 2 и больше): практики считают \
             круглую цену признаком настоящей плотности.",
        ),
        item(
            "Завал",
            &format!(
                "сколько живых уровней не ниже порога H3 стоит на той же стороне не дальше \
                 {DISTANCE_MAX_BPS} bps от плотности в момент касания, включая её саму (В-45). \
                 При отладочном пороге это почти весь стакан — число вырождено и не \
                 показывается; при заглушке k = {K_STUB} (В-30) рядом с числом так и написано."
            ),
        ),
    ]
}

// ---------------------------------------------------------------------------
// Страница
// ---------------------------------------------------------------------------

/// Страница целиком — отдельный файл рядом (`dashboard_page.html`): правка
/// вёрстки или JS не трогает Rust и не требует читать его; плейсхолдеры
/// `__REFRESH_SECS__` и `__BOOTSTRAP_JSON__` подставляет `render_html`.
const PAGE_TEMPLATE: &str = include_str!("dashboard_page.html");

/// Подставляет данные в шаблон. JSON кладётся в `<script type=
/// "application/json">` — так страница открывается и с диска (`file://`, где
/// `fetch` относительного пути запрещён браузером), и с `http.server`, где
/// тот же `fetch` раз в 30 секунд подменяет данные свежими.
fn render_html(json: &str) -> String {
    // `<` внутри JSON закрыл бы тег `script` раньше времени, если в данных
    // окажется строка `</script>` (например, в сообщении об ошибке).
    let safe = json.replace('<', "\\u003c");
    PAGE_TEMPLATE
        .replace("__REFRESH_SECS__", &PAGE_REFRESH_SECS.to_string())
        .replace("__BOOTSTRAP_JSON__", &safe)
}

// ---------------------------------------------------------------------------
// Тесты
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests;
