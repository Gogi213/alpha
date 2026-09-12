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
//!    `lob profiles` для этой монеты.
//!
//! Разметка уровней **не переписана**: `replay_symbol` — тот же реплей, что
//! у `lob levels`/`markout`/`watch`/`pilot`; `markouts_for_level` и
//! `costs::observation_at` — те же функции, что считают артефакты; корзины
//! осей — `shortlist::*_LABELS` и границы из `profiles`. Число на странице
//! получено тем же кодом, что число в CSV.
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
use crate::lob::levels::{H3Mode, LevelsConfig, LiveLevel, Outcome};
use crate::lob::markout::{
    markouts_for_level, mid_double_tick, raw_return_bps, MidSample, HORIZONS_MS,
};
use crate::lob::shortlist::{
    repeat_bucket, DISTANCE_LABELS, LIFETIME_LABELS, REPEAT_LABELS, SIDE_LABELS, SIZE_LABELS,
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
        picture: Picture {
            from_ms: 0,
            to_ms: 0,
            mid: Vec::new(),
            bars: Vec::new(),
            bars_total: 0,
            price_lo: 0.0,
            price_hi: 0.0,
            clipped: 0,
        },
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

fn labelled(labels: &[&'static str]) -> Vec<(&'static str, Acc)> {
    labels.iter().map(|l| (*l, Acc::default())).collect()
}

fn push_labelled(accs: &mut [(&str, Acc)], label: &str, s: &Sample) {
    if let Some((_, acc)) = accs.iter_mut().find(|(l, _)| *l == label) {
        acc.push(s);
    }
}

fn rows(accs: &[(&str, Acc)], total: usize) -> Vec<Row> {
    accs.iter().map(|(l, a)| a.row(l, total)).collect()
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
        return Picture {
            from_ms: 0,
            to_ms: 0,
            mid: Vec::new(),
            bars: Vec::new(),
            bars_total: 0,
            price_lo: 0.0,
            price_hi: 0.0,
            clipped: 0,
        };
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
    Picture {
        from_ms,
        to_ms,
        mid,
        bars,
        bars_total,
        price_lo,
        price_hi,
        clipped,
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
mod tests {
    use super::*;
    use crate::commands::lob::test_support::{
        delta_frame, snap_frame, trade_frame, write_day_part,
    };

    fn write_instruments_csv(root: &Path, symbols: &[&str]) {
        let mut s = String::from(
            "symbol,turnover_usd_e9,tick_e9,step_e9,h3_lots,k,median_trade_lots,\
             window_start_utc_ms,window_secs,final_rank,selected_for_pilot\n",
        );
        for (i, sym) in symbols.iter().enumerate() {
            s.push_str(&format!(
                "{sym},1,10000000,1000000,5,1.0,5,0,3600,{},true\n",
                i + 1
            ));
        }
        std::fs::write(root.join("instruments.csv"), s).unwrap();
    }

    fn write_session_json(root: &Path, symbols: &[&str], started: &str, closed: bool) {
        let json = serde_json::json!({
            "started_utc": started,
            "start_hour_utc": 12,
            "duration_s": 3600u64,
            "instruments": symbols,
            "records_total": 1000u64,
            "gaps": 0u64,
            "clock_samples": 1u64,
            "parse_p99_ns": 50_000i64,
            "queue_p99_ns": 300_000i64,
            "cpu_pct_avg": 1.9,
            "cpu_pct_max": 3.5,
            "rss_bytes_start": 5_000_000u64,
            "rss_bytes_end": 6_000_000u64,
            "out": root.display().to_string(),
            "debug": false,
            "pilot": false,
            "pilot_minutes": serde_json::Value::Null,
            "always_on": true,
            "reconnects": 0u64,
            "resyncs": 0u64,
            "frames_failed": 0u64,
            "bytes_written": 4096u64,
            "updated_utc": started,
            "closed": closed,
            "samples": [{"ts_utc": started, "rss_bytes": 6_000_000u64, "cpu_pct": 2.0}],
            "binlog_files": [
                {"symbol": symbols[0], "part": 1, "started_utc": started},
            ],
        });
        std::fs::write(
            root.join("session.json"),
            serde_json::to_string_pretty(&json).unwrap(),
        )
        .unwrap();
    }

    /// Книга 100.00/100.01 (тик 0.01), порог 5 лотов. Уровень на 10000
    /// (бид, 10 лотов, 2×H3) живёт с 0 до 20 с и проедается сделкой в 8
    /// лотов; уровень на 9999 (бид, 10 лотов) живёт с 0 и снимается на 30 с;
    /// уровень на 10002 (аск, 30 лотов, 6×H3) рождается на 25 с и **жив** до
    /// конца записи (60 с), как и аск 10001 (10 лотов) с нуля.
    fn frames() -> Vec<Vec<crate::commands::lob::Record>> {
        vec![
            snap_frame(0, &[(9998, 1), (9999, 10), (10000, 10)], &[(10001, 10)]),
            trade_frame(10_000, 10000, 8),
            delta_frame(20_000, &[(10000, 1)], &[]),
            delta_frame(25_000, &[], &[(10002, 30)]),
            delta_frame(30_000, &[(9999, 1)], &[]),
            delta_frame(60_000, &[(9998, 2)], &[]),
        ]
    }

    fn fixture(dir: &Path) -> DashboardArgs {
        write_instruments_csv(dir, &["SOLUSDT"]);
        write_session_json(dir, &["SOLUSDT"], "2026-09-12T12:00:00Z", false);
        write_day_part(dir, "SOLUSDT", "2026-09-12", 1, &frames());
        DashboardArgs {
            root: dir.to_path_buf(),
            out: dir.join("out"),
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            h3_k: None,
            watch: None,
        }
    }

    /// Живой уровень на последнем кадре попадает в «плотности сейчас» с
    /// расстоянием, размером и возрастом от последней середины; умершие —
    /// в исходы; полоски картины несут все три.
    #[test]
    fn coin_page_shows_live_levels_outcomes_and_bars() {
        let dir = tempfile::tempdir().unwrap();
        let args = fixture(dir.path());
        let d = build_dashboard(&args).expect("расчёт обязан пройти на фикстуре");
        assert_eq!(d.coins.len(), 1);
        let c = &d.coins[0];
        assert!(
            c.error.is_none(),
            "реплей фикстуры обязан пройти: {:?}",
            c.error
        );
        assert_eq!(c.h3_lots, 5);
        assert_eq!(c.price_decimals, 2, "тик 0.01 — два знака");
        assert_eq!(
            c.bid,
            Some(100.0),
            "лучший бид на последнем кадре — 10000 тиков"
        );
        assert_eq!(c.ask, Some(100.01));

        // Живы два аска: 10001 (10 лотов, с нуля) и 10002 (30 лотов, с 25 с);
        // самый крупный — первый.
        assert_eq!(c.levels_now.len(), 2, "{:?}", c.levels_now);
        assert_eq!(c.levels_now[1].size_lots, 10);
        let now = &c.levels_now[0];
        assert_eq!(now.side, "ask");
        assert_eq!(now.size_lots, 30);
        assert!((now.size_x_h3 - 6.0).abs() < 1e-9);
        assert!(now.distance_bps.unwrap() > 0.0);
        assert_eq!(now.age_secs, 35, "родился на 25 с, последний кадр 60 с");

        assert_eq!(c.levels_total, 2, "два умерших уровня");
        let by = |l: &str| c.outcomes.iter().find(|r| r.label == l).unwrap().n;
        assert_eq!(by("eaten"), 1);
        assert_eq!(by("pulled"), 1);
        assert_eq!(c.all.n, 2);

        let alive = c.picture.bars.iter().filter(|b| b.o == "alive").count();
        assert_eq!(alive, 2);
        assert_eq!(c.picture.bars.len(), 4, "два умерших и два живых");
        assert_eq!(c.picture.to_ms, 60_000);
        assert!(!c.picture.mid.is_empty());
    }

    /// Оба файла лежат на диске, JSON разбирается обратно в те же типы, а
    /// временных файлов после записи не остаётся.
    #[test]
    fn renders_both_files_atomically_and_json_round_trips() {
        let dir = tempfile::tempdir().unwrap();
        let args = fixture(dir.path());
        let s = run_dashboard(&args).unwrap();
        assert!(s.json.exists() && s.html.exists());
        assert!(!args.out.join("data.tmp").exists());
        assert!(!args.out.join("index.tmp").exists());
        let raw = std::fs::read_to_string(&s.json).unwrap();
        let d: Dashboard = serde_json::from_str(&raw).unwrap();
        assert_eq!(d.coins[0].symbol, "SOLUSDT");
        assert_eq!(d.assumed_rtt_ms, ASSUMED_RTT_MS);
        assert_eq!(d.roundtrip_fees_bps, ROUNDTRIP_FEES_BPS);
        assert_eq!(s.coins, 1);
    }

    /// Жив/нет: первый расчёт — «не знаю», второй с выросшими бинлогами —
    /// «пишет», без роста — «не пишет»; чужой `--root` в том же `--out` за
    /// базу не берётся.
    #[test]
    fn liveness_comes_from_binlog_growth_between_two_renders() {
        let dir = tempfile::tempdir().unwrap();
        let args = fixture(dir.path());
        let first = run_dashboard(&args).unwrap();
        assert_eq!(first.alive, None);

        let second = run_dashboard(&args).unwrap();
        assert_eq!(second.alive, Some(false), "бинлоги не выросли");

        // Дописали часть — выросли.
        write_day_part(dir.path(), "SOLUSDT", "2026-09-12", 2, &frames());
        let third = run_dashboard(&args).unwrap();
        assert_eq!(third.alive, Some(true), "{}", third.alive_reason);

        // Другой каталог в тот же --out: базы нет.
        let other = tempfile::tempdir().unwrap();
        let mut args2 = fixture(other.path());
        args2.out = args.out.clone();
        let fourth = run_dashboard(&args2).unwrap();
        assert_eq!(fourth.alive, None, "чужой data.json не база");
    }

    /// `closed = true` — «остановлен», независимо от роста.
    #[test]
    fn closed_session_is_reported_as_stopped() {
        let dir = tempfile::tempdir().unwrap();
        let args = fixture(dir.path());
        write_session_json(dir.path(), &["SOLUSDT"], "2026-09-12T12:00:00Z", true);
        let s = run_dashboard(&args).unwrap();
        assert_eq!(s.alive, Some(false));
        assert!(s.alive_reason.contains("остановлен"), "{}", s.alive_reason);
    }

    /// Монета без бинлога не роняет страницу: строка с ошибкой, соседи целы.
    #[test]
    fn a_coin_without_binlog_is_reported_not_dropped() {
        let dir = tempfile::tempdir().unwrap();
        let mut args = fixture(dir.path());
        write_instruments_csv(dir.path(), &["SOLUSDT", "XRPUSDT"]);
        write_session_json(
            dir.path(),
            &["SOLUSDT", "XRPUSDT"],
            "2026-09-12T12:00:00Z",
            false,
        );
        args.h3_k = None;
        let d = build_dashboard(&args).unwrap();
        assert_eq!(d.coins.len(), 2);
        assert!(d.coins[0].error.is_none());
        let e = d.coins[1]
            .error
            .as_deref()
            .expect("XRPUSDT без файла — ошибка");
        assert!(e.contains("XRPUSDT"), "{e}");
    }

    /// Критерий приёмки: страница самодостаточна — ни одного внешнего URL,
    /// и JSON не может закрыть тег `script`.
    #[test]
    fn page_is_self_contained_html_without_external_urls() {
        let html = render_html(r#"{"x":"</script><b>"}"#);
        for needle in ["http://", "https://", "src=\"//", "@import"] {
            assert!(
                !html.contains(needle),
                "внешняя ссылка на странице: {needle}"
            );
        }
        assert!(!html.contains("</script><b>"), "JSON закрыл script");
        assert!(html.contains("\\u003c/script>"));
        assert!(html.contains("<title>Монеты и плотности</title>"));
    }

    #[test]
    fn decimals_follow_the_tick() {
        assert_eq!(decimals_of_e9(10_000_000), 2);
        assert_eq!(decimals_of_e9(1_000_000_000), 0);
        assert_eq!(decimals_of_e9(1), 9);
        assert_eq!(decimals_of_e9(0), 0);
    }

    #[test]
    fn gaps_are_counted_from_the_live_csv_without_header() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("gaps.csv");
        assert_eq!(gaps_rows(&p), 0);
        std::fs::write(&p, "symbol,kind,ts\n").unwrap();
        assert_eq!(gaps_rows(&p), 0);
        std::fs::write(&p, "symbol,kind,ts\nSOLUSDT,seq,1\nSOLUSDT,seq,2\n").unwrap();
        assert_eq!(gaps_rows(&p), 2);
    }
}
