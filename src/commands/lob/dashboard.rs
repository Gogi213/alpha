//! `lob dashboard` — минимальная визуализация проекта на localhost (таск 32,
//! R87: «мне нужна минимальная визуализация проекта потому что я пока ничего
//! не понимаю, только с твоих слов. надо повесить что то на локалхост чтобы я
//! хоть что то понимал»).
//!
//! Команда **только читает**: живой каталог олвейс-он коллектора
//! (`--root`) не изменяется ни одним байтом, всё пишется в `--out`
//! (`index.html` + `data.json`). Это условие живого прогона — коллектор
//! держит те же файлы открытыми на запись.
//!
//! Четыре блока страницы ровно в том порядке, в каком их назвал владелец:
//! «Коллектор сейчас» (жив ли, сколько пишет, в норме ли по гейту GC
//! `PLAN.md` 6.1), «Контрольные точки 30 мин / 1 ч / 12 ч» (В-38 —
//! промежуточные выводы по накопленному), «По инструментам» (уровни, исходы,
//! markout — считаются здесь же реплеем бинлогов), «Где мы» (гейты, вердикт
//! пилота, аудит, что дальше — статический текст с датой, обновляется тем
//! таском, который меняет состояние).
//!
//! Разметка уровней **не переписана**: `replay_symbol` — тот же реплей, что
//! у `lob levels`/`markout`/`watch`/`pilot`, `markouts_for_level`/
//! `base_before`/`mean_net_bps` — те же функции, что считают артефакты. Число
//! на странице получено тем же кодом, что число в CSV, иначе страница была бы
//! вторым источником правды.
//!
//! Ни одного порога эта команда не изобретает: каждый — из `PLAN.md` 6.1,
//! `SETTLED.md` или задачи, и вместе с числом печатается ссылка на источник.

use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Args;

use crate::commands::record::{FRAME_LOSS_WINDOW_SECS, HOURLY_REFRESH_SECS};
use crate::lob::costs::{mean_net_bps, Observation, ROUNDTRIP_FEES_BPS};
use crate::lob::levels::{LevelRecord, LevelsConfig, Outcome};
use crate::lob::markout::{base_before, markouts_for_level, MidSample, HORIZONS_MS};
use crate::stats::{count_f64, count_f64_u64, G_MIN};

use super::session::SessionSummary;
use super::{
    replay_symbol, resolve_h3_mode, session_days_in_dir, H3ModeArg, DEFAULT_REPEAT_WINDOW_MS,
    DEFAULT_WARMUP_MS,
};

// ---------------------------------------------------------------------------
// Пороги. Каждый — цитата из документа, ни одного изобретённого числа.
// ---------------------------------------------------------------------------

/// Гейт GC (`PLAN.md` 6.1): «CPU — прогон по всем десяти, < 5% ядра суммарно».
const GC_CPU_PCT_MAX: f64 = 5.0;

/// Гейт GC (`PLAN.md` 6.1): «задержка разбора `p99 < 200 мкс`».
const GC_PARSE_P99_NS_MAX: i64 = 200_000;

/// Гейт GC (`PLAN.md` 6.1): «часы — `clock.csv` не реже раза за прогон;
/// |offset| < 5 мс».
const GC_NTP_OFFSET_MS_MAX: f64 = 5.0;

/// Замер таска 24/25 (`docs/findings/collector-2026-09-12.md`, `SETTLED.md`
/// В-34): 7.27 байта на запись на живом олвейс-он прогоне — база, от которой
/// D-ДИСК считает регрессию.
const BYTES_PER_RECORD_BASELINE: f64 = 7.27;

/// D-ДИСК (`PLAN.md` 4.2, В-34): «байт на запись меряется и держится как
/// порог регрессии > 10%».
const D_DISK_REGRESSION_FRACTION: f64 = 0.10;

/// В-38: контрольные точки промежуточных выводов — 30 минут, 1 час, 12 часов
/// («первые сутки и 7 суток не нужны. меняем первые сутки и 7 суток на
/// полчаса-час-12 часов»).
const CHECKPOINT_MINUTES: [u32; 3] = [30, 60, 720];

/// В-37: задержка круга принята владельцем за 20 мс до замера («ну допустим
/// задержка на глаз 20 мс, пока у нас нет сервера байбита, просто примем за
/// факт»); всякий артефакт с этим числом помечается `assumed`, не `measured`.
const ASSUMED_RTT_MS: u32 = 20;

/// Как часто страница сама перечитывает `data.json` (секунды) — критерий
/// приёмки таска 32.
const PAGE_REFRESH_SECS: u64 = 30;

/// Дата, на которую верен статический блок «Где мы». Меняет её тот таск,
/// который меняет состояние проекта, — не этот код по своей инициативе.
const WHERE_WE_ARE_AS_OF: &str = "2026-09-12";

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Args)]
pub struct DashboardArgs {
    /// Каталог коллектора (олвейс-он или любой сессии): `session.json`,
    /// `<SYMBOL>-<день>[-pN].binlog`, `clock.csv`. Только чтение.
    #[arg(long)]
    pub root: PathBuf,

    /// Куда положить `index.html` и `data.json`. Каталог создаётся.
    #[arg(long)]
    pub out: PathBuf,

    /// Режим порога `H3`. В отличие от `levels`/`markout`/`profiles` здесь
    /// есть умолчание `floor`, и это не отступление от правила «умолчания
    /// нет»: дашборд ничего не решает и не пишет в артефакты вердикта, а
    /// `percentile` требует часа прогрева (`--warmup-ms`, умолчание
    /// 3 600 000) и на записи короче часа даёт `levels = 0` — страница,
    /// открытая владельцем на тридцатой минуте, была бы пуста.
    #[arg(long, value_enum, default_value = "floor")]
    pub h3_mode: H3ModeArg,

    /// Порог в лотах для `--h3-mode percentile` (у `floor` берётся из
    /// `instruments.csv` в `--root`, как у всех остальных подкоманд).
    #[arg(long)]
    pub h3_lots: Option<i64>,

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
    pub instruments: usize,
    pub alive: Option<bool>,
    pub alive_reason: String,
}

// ---------------------------------------------------------------------------
// Форма `data.json`
// ---------------------------------------------------------------------------

/// Вердикт одной проверки: есть порог и он пройден, есть порог и не пройден,
/// порога числом в документах нет (тогда число печатается без цвета — цвет
/// без порога и есть изобретённое число).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    Ok,
    Bad,
    NoThreshold,
    Unknown,
}

/// Одно число блока 1 со всем, что нужно, чтобы его понять без объяснений
/// «на словах»: подпись, значение, что это простыми словами, порог и откуда
/// порог взят.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Check {
    pub label: String,
    pub value: String,
    pub what: String,
    pub threshold: String,
    pub source: String,
    pub verdict: Verdict,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Collector {
    pub alive: Option<bool>,
    pub alive_reason: String,
    pub started_utc: String,
    pub updated_utc: String,
    pub closed: bool,
    pub always_on: bool,
    pub session_json_age_secs: Option<i64>,
    pub recorded_secs: i64,
    pub instruments: Vec<String>,
    pub records_total: u64,
    pub bytes_on_disk: u64,
    pub bytes_growth: Option<i64>,
    pub growth_window_secs: Option<i64>,
    pub days: Vec<String>,
    pub checks: Vec<Check>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Checkpoint {
    pub minutes: u32,
    pub label: String,
    pub reached: bool,
    pub levels_total: usize,
    pub levels_per_hour_median: Option<f64>,
    pub eaten_share_median: Option<f64>,
    pub m10s_eaten_bps: Option<f64>,
    pub m10s_pulled_bps: Option<f64>,
    pub m10s_mixed_bps: Option<f64>,
    pub net_bps: Option<f64>,
    pub net_observations: usize,
    pub conclusion: String,
    pub caveat: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct InstrumentRow {
    pub symbol: String,
    pub records: u64,
    pub bytes: u64,
    pub kb_per_min: Option<f64>,
    pub levels: usize,
    pub levels_per_hour: Option<f64>,
    pub share_eaten: Option<f64>,
    pub share_pulled: Option<f64>,
    pub share_mixed: Option<f64>,
    pub m10s_eaten_bps: Option<f64>,
    pub m10s_pulled_bps: Option<f64>,
    pub lifetime_median_ms: Option<f64>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GateRow {
    pub gate: String,
    pub status: String,
    pub verdict: Verdict,
    pub what: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TextItem {
    pub title: String,
    pub body: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct WhereWeAre {
    pub as_of: String,
    pub gates: Vec<GateRow>,
    pub pilot: Vec<TextItem>,
    pub audit: Vec<TextItem>,
    pub next: Vec<TextItem>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Dashboard {
    pub generated_utc: String,
    pub root: String,
    pub h3_mode: String,
    pub assumed_rtt_ms: u32,
    pub tail_not_visible_secs: u64,
    pub collector: Collector,
    pub checkpoints: Vec<Checkpoint>,
    pub instruments: Vec<InstrumentRow>,
    pub glossary: Vec<TextItem>,
    pub where_we_are: WhereWeAre,
}

// ---------------------------------------------------------------------------
// Расчёт
// ---------------------------------------------------------------------------

/// Один расчёт или бесконечный цикл с шагом `--watch`. Возвращает итог
/// последнего расчёта; в режиме `--watch` не возвращается, пока команду не
/// остановят (Ctrl+C — тот же способ остановки, что у коллектора).
pub fn run_dashboard(args: &DashboardArgs) -> anyhow::Result<DashboardSummary> {
    if let Some(secs) = args.watch {
        anyhow::ensure!(secs > 0, "--watch <секунды> обязан быть положителен");
        loop {
            let summary = render_once(args)?;
            eprintln!(
                "dashboard: {} — следующий расчёт через {secs} с (Ctrl+C — остановить)",
                summary.alive_reason
            );
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
    let json = serde_json::to_string_pretty(&data)?;
    std::fs::write(&json_path, &json)
        .map_err(|e| anyhow::anyhow!("не записать {}: {e}", json_path.display()))?;
    std::fs::write(&html_path, render_html(&json))
        .map_err(|e| anyhow::anyhow!("не записать {}: {e}", html_path.display()))?;
    Ok(DashboardSummary {
        html: html_path,
        json: json_path,
        instruments: data.instruments.len(),
        alive: data.collector.alive,
        alive_reason: data.collector.alive_reason,
    })
}

/// Читает каталог и собирает всю страницу как данные. Отдельно от записи
/// файлов — тесты проверяют числа, а не ввод-вывод.
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
    let now_ns = now.timestamp_nanos_opt().unwrap_or(0);
    let started_ns = rfc3339_ns(&summary.started_utc);
    let updated_ns = rfc3339_ns(&summary.updated_utc);

    let previous = read_previous(&args.out.join("data.json"));

    let bytes_on_disk = binlog_bytes_on_disk(&args.root);
    let (bytes_growth, growth_window_secs) = match previous.as_ref() {
        Some(prev) => {
            let window = rfc3339_ns(&prev.generated_utc).map(|p| (now_ns - p) / 1_000_000_000);
            (
                Some(
                    i64::try_from(bytes_on_disk).unwrap_or(i64::MAX)
                        - i64::try_from(prev.collector.bytes_on_disk).unwrap_or(i64::MAX),
                ),
                window,
            )
        }
        None => (None, None),
    };

    let session_json_age_secs = updated_ns.map(|u| (now_ns - u) / 1_000_000_000);
    // Длина записи: у остановленной — то, что записано в `session.json`; у
    // живой — от старта до «сейчас» (`duration_s` переписывается раз в час, и
    // на сороковой минуте второго часа он врал бы в меньшую сторону).
    let recorded_secs = if summary.closed {
        i64::try_from(summary.duration_s).unwrap_or(i64::MAX)
    } else {
        started_ns
            .map(|s| (now_ns - s) / 1_000_000_000)
            .unwrap_or_else(|| i64::try_from(summary.duration_s).unwrap_or(i64::MAX))
    };

    let (alive, alive_reason) = liveness(
        &summary,
        bytes_growth,
        growth_window_secs,
        session_json_age_secs,
    );

    let days: Vec<String> = session_days_in_dir(&args.root).into_iter().collect();
    let ntp_offset_ms = last_ntp_offset_ms(&args.root.join("clock.csv"));

    let checks = build_checks(&summary, bytes_on_disk, recorded_secs, ntp_offset_ms);

    let collector = Collector {
        alive,
        alive_reason: alive_reason.clone(),
        started_utc: summary.started_utc.clone(),
        updated_utc: summary.updated_utc.clone(),
        closed: summary.closed,
        always_on: summary.always_on,
        session_json_age_secs,
        recorded_secs,
        instruments: summary.instruments.clone(),
        records_total: summary.records_total,
        bytes_on_disk,
        bytes_growth,
        growth_window_secs,
        days: days.clone(),
        checks,
    };

    // Реплей: один проход на инструмент, из него и таблица блока 3, и все
    // три контрольные точки блока 2 (фильтр по `birth_ms`).
    let mut instruments: Vec<InstrumentRow> = Vec::new();
    let mut replays: Vec<(String, Vec<DayRecords>)> = Vec::new();
    let recorded_minutes = count_f64_u64(u64::try_from(recorded_secs.max(0)).unwrap_or(0)) / 60.0;
    for symbol in &summary.instruments {
        match replay_one(&args.root, symbol, args.h3_mode, args.h3_lots) {
            Ok(days) => {
                instruments.push(instrument_row(symbol, &days, recorded_minutes));
                replays.push((symbol.clone(), days));
            }
            Err(e) => instruments.push(InstrumentRow {
                symbol: symbol.clone(),
                records: 0,
                bytes: 0,
                kb_per_min: None,
                levels: 0,
                levels_per_hour: None,
                share_eaten: None,
                share_pulled: None,
                share_mixed: None,
                m10s_eaten_bps: None,
                m10s_pulled_bps: None,
                lifetime_median_ms: None,
                error: Some(e.to_string()),
            }),
        }
    }

    let checkpoints = build_checkpoints(&replays, recorded_secs, days.len());

    Ok(Dashboard {
        generated_utc: now.to_rfc3339(),
        root: args.root.display().to_string(),
        h3_mode: match args.h3_mode {
            H3ModeArg::Floor => "floor".to_string(),
            H3ModeArg::Percentile => "percentile".to_string(),
        },
        assumed_rtt_ms: ASSUMED_RTT_MS,
        tail_not_visible_secs: FRAME_LOSS_WINDOW_SECS,
        collector,
        checkpoints,
        instruments,
        glossary: glossary(),
        where_we_are: where_we_are(),
    })
}

/// Записи и срезы середины одних суток — то, что вернул `replay_symbol`,
/// плюс байты и число записей файла (для КБ/мин).
struct DayRecords {
    records: Vec<LevelRecord>,
    mids: Vec<MidSample>,
    bytes: u64,
    decoded_records: u64,
}

/// Реплей одного инструмента тем же кодом, что `lob levels`/`markout`
/// (`replay_symbol`), — второй разметки здесь нет.
fn replay_one(
    root: &Path,
    symbol: &str,
    mode: H3ModeArg,
    h3_lots: Option<i64>,
) -> anyhow::Result<Vec<DayRecords>> {
    let h3 = resolve_h3_mode(root, symbol, mode, h3_lots)?;
    let cfg = LevelsConfig {
        mode: h3,
        warmup_ms: DEFAULT_WARMUP_MS,
        repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
    };
    let stats = replay_symbol(root, symbol, cfg)?;
    let bytes = stats.bytes;
    let decoded = stats.records;
    let n_days = stats.days.len().max(1);
    Ok(stats
        .days
        .into_iter()
        .map(|d| DayRecords {
            records: d.records,
            mids: d.mids,
            // Байты и записи `replay_symbol` считает по всем частям символа
            // разом; на сутки они делятся поровну только чтобы строка
            // таблицы (одна на инструмент) сложила их обратно без потерь.
            bytes: bytes / u64::try_from(n_days).unwrap_or(1),
            decoded_records: decoded / u64::try_from(n_days).unwrap_or(1),
        })
        .collect())
}

/// Строка блока 3: всё считается по полной записи (не по контрольной точке).
fn instrument_row(symbol: &str, days: &[DayRecords], recorded_minutes: f64) -> InstrumentRow {
    let records: u64 = days.iter().map(|d| d.decoded_records).sum();
    let bytes: u64 = days.iter().map(|d| d.bytes).sum();
    let agg = aggregate(days, None);
    InstrumentRow {
        symbol: symbol.to_string(),
        records,
        bytes,
        kb_per_min: if recorded_minutes > 0.0 {
            Some(count_f64_u64(bytes) / 1024.0 / recorded_minutes)
        } else {
            None
        },
        levels: agg.levels,
        levels_per_hour: if recorded_minutes > 0.0 {
            Some(count_f64(agg.levels) / recorded_minutes * 60.0)
        } else {
            None
        },
        share_eaten: agg.share(Outcome::Eaten),
        share_pulled: agg.share(Outcome::Pulled),
        share_mixed: agg.share(Outcome::Mixed),
        m10s_eaten_bps: mean(&agg.m10s_eaten),
        m10s_pulled_bps: mean(&agg.m10s_pulled),
        lifetime_median_ms: median_f64(&agg.lifetimes),
        error: None,
    }
}

/// Свёртка одного инструмента (или пула) по срезу времени.
#[derive(Default)]
struct Agg {
    levels: usize,
    eaten: usize,
    pulled: usize,
    mixed: usize,
    m10s_eaten: Vec<f64>,
    m10s_pulled: Vec<f64>,
    m10s_mixed: Vec<f64>,
    lifetimes: Vec<f64>,
    observations: Vec<Observation>,
}

impl Agg {
    fn share(&self, outcome: Outcome) -> Option<f64> {
        if self.levels == 0 {
            return None;
        }
        let n = match outcome {
            Outcome::Eaten => self.eaten,
            Outcome::Pulled => self.pulled,
            Outcome::Mixed => self.mixed,
        };
        Some(count_f64(n) / count_f64(self.levels))
    }

    fn merge(&mut self, other: &Agg) {
        self.levels += other.levels;
        self.eaten += other.eaten;
        self.pulled += other.pulled;
        self.mixed += other.mixed;
        self.m10s_eaten.extend_from_slice(&other.m10s_eaten);
        self.m10s_pulled.extend_from_slice(&other.m10s_pulled);
        self.m10s_mixed.extend_from_slice(&other.m10s_mixed);
        self.lifetimes.extend_from_slice(&other.lifetimes);
        self.observations.extend_from_slice(&other.observations);
    }
}

/// Считает исходы, markout на 10 с и наблюдения для `net` по уровням,
/// родившимся не позже `cutoff_ms` (`None` — вся запись). Индекс `2` в
/// `markouts_for_level` — горизонт 10 000 мс (`HORIZONS_MS`), тот же, что
/// берёт `lob pilot`.
fn aggregate(days: &[DayRecords], cutoff_ms: Option<i64>) -> Agg {
    debug_assert_eq!(
        HORIZONS_MS[2], 10_000,
        "индекс горизонта 10 с обязан указывать на 10 000 мс"
    );
    let mut agg = Agg::default();
    for day in days {
        for rec in &day.records {
            if cutoff_ms.is_some_and(|c| rec.birth_ms > c) {
                continue;
            }
            agg.levels += 1;
            let outcome = rec.outcome();
            match outcome {
                Outcome::Eaten => agg.eaten += 1,
                Outcome::Pulled => agg.pulled += 1,
                Outcome::Mixed => agg.mixed += 1,
            }
            #[allow(clippy::cast_precision_loss)]
            agg.lifetimes.push(rec.lifetime_ms as f64);
            let Some(m) = markouts_for_level(rec, &day.mids)[2] else {
                continue;
            };
            match outcome {
                Outcome::Eaten => agg.m10s_eaten.push(m),
                Outcome::Pulled => agg.m10s_pulled.push(m),
                Outcome::Mixed => agg.m10s_mixed.push(m),
            }
            // Наблюдение `net` — та же конструкция, что в `lob pilot`:
            // markout минус издержки круга и проскальзывание по спреду в
            // момент выхода.
            let Some((base_ts, base2x)) = base_before(&day.mids, rec.death_ms) else {
                continue;
            };
            let target = base_ts.saturating_add(HORIZONS_MS[2]);
            let mut exit: Option<MidSample> = None;
            for s in &day.mids {
                if s.ts_ms <= target {
                    exit = Some(*s);
                } else {
                    break;
                }
            }
            if let Some(x) = exit {
                agg.observations.push(Observation {
                    m_bps: m,
                    spread_ticks_exit: x.ask_tick - x.bid_tick,
                    mid2x_base: base2x,
                });
            }
        }
    }
    agg
}

/// Блок 2: три контрольные точки В-38 по времени записи.
fn build_checkpoints(
    replays: &[(String, Vec<DayRecords>)],
    recorded_secs: i64,
    days_recorded: usize,
) -> Vec<Checkpoint> {
    // Начало записи в шкале бинлога: самый ранний срез середины по пулу.
    let start_ms = replays
        .iter()
        .flat_map(|(_, days)| days.iter())
        .filter_map(|d| d.mids.first().map(|s| s.ts_ms))
        .min();

    CHECKPOINT_MINUTES
        .iter()
        .map(|&minutes| {
            let reached = recorded_secs >= i64::from(minutes) * 60;
            let label = checkpoint_label(minutes);
            if !reached {
                let left = i64::from(minutes) * 60 - recorded_secs;
                return Checkpoint {
                    minutes,
                    label,
                    reached: false,
                    levels_total: 0,
                    levels_per_hour_median: None,
                    eaten_share_median: None,
                    m10s_eaten_bps: None,
                    m10s_pulled_bps: None,
                    m10s_mixed_bps: None,
                    net_bps: None,
                    net_observations: 0,
                    conclusion: format!(
                        "не достигнута: записано {}, осталось {}",
                        human_duration(recorded_secs),
                        human_duration(left.max(0))
                    ),
                    caveat: String::new(),
                };
            }
            let cutoff = start_ms.map(|s| s + i64::from(minutes) * 60_000);
            let mut pooled = Agg::default();
            let mut rates: Vec<f64> = Vec::new();
            let mut eaten_shares: Vec<f64> = Vec::new();
            for (_, days) in replays {
                let agg = aggregate(days, cutoff);
                rates.push(count_f64(agg.levels) / f64::from(minutes) * 60.0);
                if let Some(s) = agg.share(Outcome::Eaten) {
                    eaten_shares.push(s);
                }
                pooled.merge(&agg);
            }
            let net = mean_net_bps(&pooled.observations);
            let levels_per_hour_median = median_f64(&rates);
            let eaten_share_median = median_f64(&eaten_shares);
            let m10s_eaten = mean(&pooled.m10s_eaten);
            let m10s_pulled = mean(&pooled.m10s_pulled);
            Checkpoint {
                minutes,
                label,
                reached: true,
                levels_total: pooled.levels,
                levels_per_hour_median,
                eaten_share_median,
                m10s_eaten_bps: m10s_eaten,
                m10s_pulled_bps: m10s_pulled,
                m10s_mixed_bps: mean(&pooled.m10s_mixed),
                net_bps: net,
                net_observations: pooled.observations.len(),
                conclusion: checkpoint_conclusion(
                    levels_per_hour_median,
                    eaten_share_median,
                    m10s_eaten,
                    net,
                ),
                caveat: checkpoint_caveat(days_recorded),
            }
        })
        .collect()
}

fn checkpoint_label(minutes: u32) -> String {
    match minutes {
        30 => "30 минут".to_string(),
        60 => "1 час".to_string(),
        720 => "12 часов".to_string(),
        other => format!("{other} минут"),
    }
}

/// Вывод одной фразой по числам самой точки. Слова «окупается»/«не
/// окупается» стоят ровно на знаке `net`, потому что `net` и есть «markout
/// минус издержки круга».
fn checkpoint_conclusion(
    rate: Option<f64>,
    eaten: Option<f64>,
    m10s_eaten: Option<f64>,
    net: Option<f64>,
) -> String {
    let rate_txt = rate.map_or_else(
        || "уровней нет".to_string(),
        |r| format!("медианный инструмент даёт {r:.0} уровней в час"),
    );
    let eaten_txt = eaten.map_or_else(
        || "доля съеденных не посчиталась".to_string(),
        |e| format!("съедено {:.1} % уровней", e * 100.0),
    );
    let m_txt = m10s_eaten.map_or_else(
        || "движения за съеденными нет в выборке".to_string(),
        |m| format!("цена за съеденным уровнем уходит на {m:.2} bps за 10 секунд"),
    );
    let net_txt = match net {
        Some(n) if n >= 0.0 => format!(
            "после издержек круга ({ROUNDTRIP_FEES_BPS:.1} bps комиссий плюс проскальзывание) остаётся +{n:.2} bps — движение окупает издержки"
        ),
        Some(n) => format!(
            "после издержек круга ({ROUNDTRIP_FEES_BPS:.1} bps комиссий плюс проскальзывание) остаётся {n:.2} bps — движение издержек не окупает"
        ),
        None => "чистый результат после издержек не посчитался — нет наблюдений".to_string(),
    };
    format!("{rate_txt}; {eaten_txt}; {m_txt}; {net_txt}.")
}

/// Оговорка В-38 текстом, не мелким шрифтом.
fn checkpoint_caveat(days_recorded: usize) -> String {
    format!(
        "Это вывод о рынке по накопленному, а не вердикт по §7 задачи: вердикт стоит на \
         суточных кластерах (нужно {G_MIN} суток и не меньше 100 наблюдений на профиль), \
         записано суток — {days_recorded}. Данными для вердикта эта строка не является \
         (В-38). Задержка круга принята за {ASSUMED_RTT_MS} мс (В-37, `assumed`, не \
         измерено); в это число она ещё не входит — RTT участвует в `net_fill` через \
         бэктест, которого дашборд не гоняет."
    )
}

// ---------------------------------------------------------------------------
// Блок 1: жив ли коллектор и в норме ли он
// ---------------------------------------------------------------------------

/// Жив или нет — по двум независимым признакам: флаг `closed` в
/// `session.json` и рост бинлогов на диске между двумя расчётами. Второй
/// признак и есть настоящий: `closed = false` остаётся и у процесса, убитого
/// без финализации.
fn liveness(
    summary: &SessionSummary,
    bytes_growth: Option<i64>,
    growth_window_secs: Option<i64>,
    session_json_age_secs: Option<i64>,
) -> (Option<bool>, String) {
    if summary.closed {
        return (
            Some(false),
            format!(
                "остановлен штатно: session.json закрыт (closed=true), последняя запись {}",
                summary.updated_utc
            ),
        );
    }
    match (bytes_growth, growth_window_secs) {
        (Some(g), Some(w)) if g > 0 => (
            Some(true),
            format!(
                "жив: бинлоги выросли на {} за {} между двумя расчётами страницы",
                human_bytes(u64::try_from(g).unwrap_or(0)),
                human_duration(w)
            ),
        ),
        (Some(g), Some(w)) if w > 0 => (
            Some(false),
            format!(
                "похоже, не пишет: за {} бинлоги не выросли ({g} байт), хотя session.json \
                 не закрыт — процесс мог быть убит без остановки",
                human_duration(w)
            ),
        ),
        _ => {
            let age = session_json_age_secs.unwrap_or(0);
            let stale = i64::try_from(HOURLY_REFRESH_SECS * 2).unwrap_or(i64::MAX);
            if age > stale {
                (
                    Some(false),
                    format!(
                        "похоже, не пишет: session.json не переписывался {}, а олвейс-он \
                         переписывает его раз в {}",
                        human_duration(age),
                        human_duration(i64::try_from(HOURLY_REFRESH_SECS).unwrap_or(0))
                    ),
                )
            } else {
                (
                    None,
                    format!(
                        "пока не знаю: рост бинлогов не с чем сравнить — это первый расчёт. \
                         session.json обновлён {} назад. Запустите с --watch, и ответ появится \
                         на следующем расчёте",
                        human_duration(age)
                    ),
                )
            }
        }
    }
}

/// Все числа блока 1 с подписью и порогом.
fn build_checks(
    summary: &SessionSummary,
    bytes_on_disk: u64,
    recorded_secs: i64,
    ntp_offset_ms: Option<f64>,
) -> Vec<Check> {
    let mut checks = Vec::new();
    let minutes = count_f64_u64(u64::try_from(recorded_secs.max(0)).unwrap_or(0)) / 60.0;

    checks.push(Check {
        label: "Инструментов".to_string(),
        value: format!("{}", summary.instruments.len()),
        what: "Сколько монет коллектор слушает одновременно — по одному файлу на монету."
            .to_string(),
        threshold: "порога нет".to_string(),
        source: String::new(),
        verdict: Verdict::NoThreshold,
    });

    checks.push(Check {
        label: "Записей".to_string(),
        value: human_count(summary.records_total),
        what: "Сколько событий стакана и сделок легло на диск с начала записи.".to_string(),
        threshold: "порога нет".to_string(),
        source: String::new(),
        verdict: Verdict::NoThreshold,
    });

    checks.push(Check {
        label: "Байт на диске".to_string(),
        value: human_bytes(bytes_on_disk),
        what: "Суммарный размер всех бинлогов каталога прямо сейчас.".to_string(),
        threshold: "потолка нет".to_string(),
        source: "В-32, D-ДИСК".to_string(),
        verdict: Verdict::NoThreshold,
    });

    let kb_per_min = if minutes > 0.0 {
        Some(count_f64_u64(bytes_on_disk) / 1024.0 / minutes)
    } else {
        None
    };
    checks.push(Check {
        label: "КБ в минуту".to_string(),
        value: kb_per_min.map_or_else(|| "n/a".to_string(), |v| format!("{v:.1}")),
        what: "Скорость, с которой растёт запись, по всему пулу вместе.".to_string(),
        threshold: "порога нет".to_string(),
        source: String::new(),
        verdict: Verdict::NoThreshold,
    });

    let gb_day = kb_per_min.map(|k| k * 60.0 * 24.0 / 1024.0 / 1024.0);
    checks.push(Check {
        label: "ГБ в сутки (прикидка)".to_string(),
        value: gb_day.map_or_else(|| "n/a".to_string(), |v| format!("{v:.2}")),
        what: "Сколько накопится за сутки, если скорость останется прежней. Это прикидка, \
               а не бюджет: потолка диска в проекте нет."
            .to_string(),
        threshold: "потолка нет".to_string(),
        source: "В-34, В-32".to_string(),
        verdict: Verdict::NoThreshold,
    });

    // Числитель и знаменатель — из одного снимка `session.json`
    // (`bytes_written`/`records_total` переписываются вместе раз в час), а
    // не размер файла «сейчас» на счётчик записей часовой давности: такая
    // смесь завышала бы байт/запись у живого процесса.
    let bpr = if summary.records_total > 0 {
        Some(count_f64_u64(summary.bytes_written) / count_f64_u64(summary.records_total))
    } else {
        None
    };
    let bpr_max = BYTES_PER_RECORD_BASELINE * (1.0 + D_DISK_REGRESSION_FRACTION);
    checks.push(Check {
        label: "Байт на запись".to_string(),
        value: bpr.map_or_else(|| "n/a".to_string(), |v| format!("{v:.2}")),
        what: format!(
            "Насколько экономно пишется одно событие — главное число «супер экономного» \
             коллектора; по снимку session.json на {}.",
            summary.updated_utc
        ),
        threshold: format!("<= {bpr_max:.2} (база {BYTES_PER_RECORD_BASELINE:.2} плюс 10 %)"),
        source: "В-34, D-ДИСК, docs/findings/collector-2026-09-12.md".to_string(),
        verdict: verdict_max(bpr, bpr_max),
    });

    checks.push(Check {
        label: "Разрывы (gaps)".to_string(),
        value: format!("{}", summary.gaps),
        what: "Сколько раз поток стакана рвался так, что книгу пришлось признать \
               недостоверной."
            .to_string(),
        threshold: "0".to_string(),
        source: "PLAN.md 6.1".to_string(),
        verdict: verdict_zero(summary.gaps),
    });
    checks.push(Check {
        label: "Переподключений".to_string(),
        value: format!("{}", summary.reconnects),
        what: "Сколько раз сокет биржи обрывался и поднимался заново.".to_string(),
        threshold: "0 на здоровом прогоне".to_string(),
        source: "В-34".to_string(),
        verdict: verdict_zero(summary.reconnects),
    });
    checks.push(Check {
        label: "Ресинков книги".to_string(),
        value: format!("{}", summary.resyncs),
        what: "Сколько раз книгу пришлось перезалить снимком после пропуска номера \
               обновления."
            .to_string(),
        threshold: "0 на здоровом прогоне".to_string(),
        source: "В-34".to_string(),
        verdict: verdict_zero(summary.resyncs),
    });
    checks.push(Check {
        label: "Кадров не записалось".to_string(),
        value: format!("{}", summary.frames_failed),
        what: "Сколько порций данных не удалось положить на диск — прямая потеря записи."
            .to_string(),
        threshold: "0".to_string(),
        source: "В-34".to_string(),
        verdict: verdict_zero(summary.frames_failed),
    });

    let cpu_last = summary
        .samples
        .iter()
        .rev()
        .find_map(|s| s.cpu_pct)
        .or(summary.cpu_pct_avg);
    checks.push(Check {
        label: "CPU, % ядра".to_string(),
        value: format!(
            "последний {} / средний {} / максимум {}",
            opt_f(cpu_last, 2),
            opt_f(summary.cpu_pct_avg, 2),
            opt_f(summary.cpu_pct_max, 2)
        ),
        what: "Сколько процессора съедает коллектор — «экономный» это про него.".to_string(),
        threshold: format!("< {GC_CPU_PCT_MAX:.0} %"),
        source: "PLAN.md 6.1 (гейт GC)".to_string(),
        verdict: verdict_max(summary.cpu_pct_max, GC_CPU_PCT_MAX),
    });

    let rss_last = summary.samples.last().map(|s| s.rss_bytes);
    let rss_growth = match (summary.rss_bytes_start, rss_last) {
        (Some(a), Some(b)) => Some(i64::try_from(b).unwrap_or(0) - i64::try_from(a).unwrap_or(0)),
        _ => None,
    };
    checks.push(Check {
        label: "Память (RSS)".to_string(),
        value: format!(
            "{} → {} ({})",
            summary
                .rss_bytes_start
                .map_or_else(|| "n/a".to_string(), human_bytes),
            rss_last.map_or_else(|| "n/a".to_string(), human_bytes),
            rss_growth.map_or_else(
                || "n/a".to_string(),
                |g| format!(
                    "{}{}",
                    if g >= 0 { "+" } else { "-" },
                    human_bytes(g.unsigned_abs())
                )
            )
        ),
        what: "Сколько оперативной памяти держит процесс; гейт требует, чтобы ряд был \
               плоским — то есть не рос от часа к часу."
            .to_string(),
        threshold: "«плоский»; числа порога в документах нет".to_string(),
        source: "PLAN.md 6.1 (гейт GC)".to_string(),
        verdict: Verdict::NoThreshold,
    });

    checks.push(Check {
        label: "Разбор кадра, p99".to_string(),
        value: summary
            .parse_p99_ns
            .map_or_else(|| "n/a".to_string(), |v| format!("{} мкс", v / 1000)),
        what: "Сколько времени занимает разбор одного сообщения биржи у худшего сообщения \
               из ста — это часть бюджета реакции бота."
            .to_string(),
        threshold: format!("p99 < {} мкс", GC_PARSE_P99_NS_MAX / 1000),
        source: "PLAN.md 6.1 (гейт GC)".to_string(),
        verdict: verdict_max_i64(summary.parse_p99_ns, GC_PARSE_P99_NS_MAX),
    });
    checks.push(Check {
        label: "Очередь до потока решений, p99".to_string(),
        value: summary
            .queue_p99_ns
            .map_or_else(|| "n/a".to_string(), |v| format!("{} мкс", v / 1000)),
        what: "Сколько разобранное сообщение ждёт, пока его заберёт поток решений.".to_string(),
        threshold: "в гейте нет, информационно".to_string(),
        source: "таск 24".to_string(),
        verdict: Verdict::NoThreshold,
    });

    checks.push(Check {
        label: "Замеров часов".to_string(),
        value: format!("{}", summary.clock_samples),
        what: "Сколько раз за прогон часы машины сверялись с эталоном.".to_string(),
        threshold: ">= 1 за прогон".to_string(),
        source: "PLAN.md 6.1 (гейт GC)".to_string(),
        verdict: if summary.clock_samples >= 1 {
            Verdict::Ok
        } else {
            Verdict::Bad
        },
    });
    checks.push(Check {
        label: "Смещение часов (NTP)".to_string(),
        value: ntp_offset_ms.map_or_else(|| "n/a".to_string(), |v| format!("{v:.1} мс")),
        what: "На сколько часы машины расходятся с мировым временем — на столько же врёт \
               каждая метка времени в записи."
            .to_string(),
        threshold: format!("|offset| < {GC_NTP_OFFSET_MS_MAX:.0} мс"),
        source: "PLAN.md 6.1 (гейт GC)".to_string(),
        verdict: verdict_max(ntp_offset_ms.map(f64::abs), GC_NTP_OFFSET_MS_MAX),
    });

    checks
}

fn verdict_zero(n: u64) -> Verdict {
    if n == 0 {
        Verdict::Ok
    } else {
        Verdict::Bad
    }
}

fn verdict_max(v: Option<f64>, max: f64) -> Verdict {
    match v {
        Some(x) if x <= max => Verdict::Ok,
        Some(_) => Verdict::Bad,
        None => Verdict::Unknown,
    }
}

fn verdict_max_i64(v: Option<i64>, max: i64) -> Verdict {
    match v {
        Some(x) if x <= max => Verdict::Ok,
        Some(_) => Verdict::Bad,
        None => Verdict::Unknown,
    }
}

// ---------------------------------------------------------------------------
// Чтение каталога
// ---------------------------------------------------------------------------

/// Сумма размеров всех `*.binlog` каталога. Именно она, а не
/// `session.json.bytes_written`, растёт между двумя расчётами у живого
/// процесса: `bytes_written` переписывается раз в час.
fn binlog_bytes_on_disk(root: &Path) -> u64 {
    let Ok(entries) = std::fs::read_dir(root) else {
        return 0;
    };
    entries
        .flatten()
        .filter(|e| {
            e.path()
                .extension()
                .is_some_and(|x| x.eq_ignore_ascii_case("binlog"))
        })
        .filter_map(|e| e.metadata().ok().map(|m| m.len()))
        .sum()
}

/// Последнее смещение NTP из `clock.csv`, миллисекунды. Файл читается тем же
/// `bybit::clock::read_rows`, что и везде.
fn last_ntp_offset_ms(path: &Path) -> Option<f64> {
    let rows = crate::bybit::clock::read_rows(path).ok()?;
    #[allow(clippy::cast_precision_loss)]
    rows.iter()
        .rev()
        .find_map(|r| r.ntp_offset_ns)
        .map(|ns| ns as f64 / 1_000_000.0)
}

/// Предыдущий расчёт — чтобы было с чем сравнить размер бинлогов. Битый или
/// отсутствующий файл — просто «сравнить не с чем», не ошибка.
fn read_previous(path: &Path) -> Option<Dashboard> {
    let raw = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

fn rfc3339_ns(s: &str) -> Option<i64> {
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .and_then(|t| t.timestamp_nanos_opt())
}

// ---------------------------------------------------------------------------
// Мелкая арифметика. Медиана и среднее — локальные, потому что в проекте нет
// общей `median` над `f64` (у `pilot.rs` она приватная, а зона таска 32 —
// только новый файл; `pick::depth` считает медиану целых лотов).
// ---------------------------------------------------------------------------

fn median_f64(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut v = values.to_vec();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = v.len() / 2;
    if !v.len().is_multiple_of(2) {
        Some(v[mid])
    } else {
        Some((v[mid - 1] + v[mid]) / 2.0)
    }
}

fn mean(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    Some(values.iter().sum::<f64>() / count_f64(values.len()))
}

fn opt_f(v: Option<f64>, digits: usize) -> String {
    v.map_or_else(|| "n/a".to_string(), |x| format!("{x:.digits$}"))
}

fn human_bytes(bytes: u64) -> String {
    let b = count_f64_u64(bytes);
    if bytes >= 1024 * 1024 * 1024 {
        format!("{:.2} ГБ", b / 1024.0 / 1024.0 / 1024.0)
    } else if bytes >= 1024 * 1024 {
        format!("{:.1} МБ", b / 1024.0 / 1024.0)
    } else if bytes >= 1024 {
        format!("{:.1} КБ", b / 1024.0)
    } else {
        format!("{bytes} Б")
    }
}

fn human_count(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::with_capacity(s.len() + s.len() / 3);
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(' ');
        }
        out.push(c);
    }
    out
}

fn human_duration(secs: i64) -> String {
    let s = secs.max(0);
    let h = s / 3600;
    let m = (s % 3600) / 60;
    let sec = s % 60;
    if h > 0 {
        format!("{h} ч {m} мин")
    } else if m > 0 {
        format!("{m} мин {sec} с")
    } else {
        format!("{sec} с")
    }
}

// ---------------------------------------------------------------------------
// Блок 4 «Где мы» и словарь — статический текст с датой.
// ---------------------------------------------------------------------------

fn gate(gate: &str, status: &str, verdict: Verdict, what: &str) -> GateRow {
    GateRow {
        gate: gate.to_string(),
        status: status.to_string(),
        verdict,
        what: what.to_string(),
    }
}

fn item(title: &str, body: &str) -> TextItem {
    TextItem {
        title: title.to_string(),
        body: body.to_string(),
    }
}

/// Статусы гейтов — дословно из таблицы `PLAN.md` «Точка остановки»
/// (2026-09-13) и `docs/findings/pilot-2026-09-11.md`. Изобретать статус
/// здесь запрещено: если гейт сдвинулся, его двигает тот таск, который его
/// сдвинул, и он же правит эту таблицу.
fn where_we_are() -> WhereWeAre {
    WhereWeAre {
        as_of: WHERE_WE_ARE_AS_OF.to_string(),
        gates: vec![
            gate(
                "G-DEBUG",
                "пройден",
                Verdict::Ok,
                "Вся цепочка команд (запись → сверка → уровни → markout → профили → бэктест) \
                 хоть раз прошла на живых данных без поломки. До него любой прогон против \
                 биржи был не длиннее пяти минут.",
            ),
            gate(
                "G-POWER-A",
                "пройден: требуемый Шарп 3.06 при 147 испытаниях",
                Verdict::Ok,
                "Ещё до сбора данных посчитано, какое качество сигнала нужно, чтобы результат \
                 не оказался случайной находкой при таком числе перебранных вариантов.",
            ),
            gate(
                "G0",
                "RED (edge): net −9.0 bps",
                Verdict::Bad,
                "Уровней в час хватает с запасом, но движение цены за уровнем не окупает \
                 издержки круга — по данным получасового пилота от 2026-09-11.",
            ),
            gate(
                "G1",
                "RED: eaten < 5 % при всех k, k не определим",
                Verdict::Bad,
                "Съеденных уровней слишком мало при любом пороге «крупного» из \
                 предрегистрированной сетки — значит, порог или правило 70/20 надо \
                 пересматривать, и это решение владельца.",
            ),
            gate(
                "G-POWER-B",
                "RED: замерено 2.26 против требуемых 3.06",
                Verdict::Bad,
                "Замеренное качество сигнала ниже того, что требовал G-POWER-A. По этому \
                 гейту сбор данных не начинается.",
            ),
            gate(
                "G-LAT",
                "не объявлен: 63 срабатывания из нужных 1000",
                Verdict::Unknown,
                "Скорость реакции бота от сообщения до отправки заявки измерена, но на \
                 слишком малом числе срабатываний, чтобы объявить вердикт.",
            ),
            gate(
                "GC",
                "частично: NTP 79 мс, разбор p99 400 мкс, RSS не плоский (пилот 30 мин)",
                Verdict::Bad,
                "Инженерный гейт: линт, аллокации, скорость, часы, память. Три пункта на \
                 пилоте были красными; таск 24/25 переписал горячий путь — свежие числа \
                 этого прогона в блоке «Коллектор сейчас» выше.",
            ),
        ],
        pilot: vec![
            item(
                "Что было",
                "2026-09-11 записали 30 минут всего пула (восемь монет) и прогнали боевой \
                 разбор. Это первые данные о рынке, а не отладка.",
            ),
            item(
                "Уровни есть",
                "От 26 до 390 крупных уровней в минуту на монету — редкости в данных нет.",
            ),
            item(
                "Съедают редко",
                "Съедено 4–8 % уровней, снято без сделок 85–92 %. Порог гейта G1 — 5 % на \
                 медианной монете; он не взят.",
            ),
            item(
                "Движение маленькое",
                "После съеденного уровня цена уходит на 0.34–1.23 bps за 10 секунд, а круг \
                 сделки стоит 7.5 bps комиссий плюс проскальзывание. Итог по пулу: \
                 −9.0 bps — идея в этом виде издержек не окупает.",
            ),
            item(
                "Порог «крупного» не выбрался",
                "Проверили пять порогов (k = 2, 5, 10, 20, 50): доля съеденных не растёт, а \
                 падает (4.9 % → 3.2 %). Ни один порог не проходит — «k не определим».",
            ),
            item(
                "Что это значит",
                "Красный про рынок, а не про мало данных: пороги под результат не двигали, \
                 все восемь монет записаны в журнал испытаний. Решение владельца — принять \
                 красный, продлить наблюдение или пересмотреть правило 70/20.",
            ),
        ],
        audit: vec![
            item(
                "1. Порог глубины режет пул (таск 27 — назначен)",
                "Код отсеивал монеты по толщине книги, а задача говорит брать первые десять \
                 по обороту; из-за этого в пуле восемь монет вместо десяти. Единственное \
                 прямое противоречие коду. Решение записано (В-35), правка назначена.",
            ),
            item(
                "2. net_fill ни разу не посчитан (таск 31 — назначен)",
                "Главная величина задачи — «сколько остаётся с учётом того, что заявка \
                 исполняется не всегда» — требует задержки круга, а её не измеряли. Владелец \
                 принял 20 мс как факт до замера (В-37), артефакты обязаны печатать \
                 `assumed`.",
            ),
            item(
                "3. PBO и CPCV пустые (таск 29 — назначен)",
                "Две из четырёх проверок «а не подогнали ли мы отбор под данные» пока не \
                 считаются вовсе.",
            ),
            item(
                "4. Ось «час» под непрерывной записью мертва (таск 30 — назначен)",
                "Час брался у куска записи, а кусок теперь один на сутки — проверка «зависит \
                 ли результат от часа суток» выродилась бы в «не зависит». Решение — брать \
                 час рождения уровня (В-36).",
            ),
            item(
                "5. Пилот честен",
                "Аудит не нашёл ни одного изобретённого числа и ни одного порога, \
                 подогнанного под результат.",
            ),
        ],
        next: vec![
            item(
                "Сейчас",
                "Крутится непрерывный коллектор. С накопленного уже можно считать — это и \
                 показывают блоки выше.",
            ),
            item(
                "Ждёт владельца",
                "Вопрос по пилоту открыт: принять красный, пересмотреть правило 70/20 или \
                 пересобрать пул. Данные суток этот вопрос и решат.",
            ),
            item(
                "Не сделано",
                "Суточного прогона коллектора ещё не было; боевой отбор пула на часовом окне \
                 не запускался; смещение часов машины (~80–140 мс) — настройка Windows, не \
                 код.",
            ),
        ],
    }
}

/// Каждый термин — одно предложение. Владелец читает страницу без нас.
fn glossary() -> Vec<TextItem> {
    vec![
        item(
            "Уровень",
            "Крупная заявка, стоящая в стакане на одной цене: она рождается, живёт и \
             умирает, и вся эта жизнь — одна строка в данных.",
        ),
        item(
            "Съеден (eaten)",
            "Уровень исчез потому, что его выкупили сделками — рынок в него упёрся и прошёл \
             насквозь.",
        ),
        item(
            "Снят (pulled)",
            "Уровень исчез потому, что заявку отменили, не дав по ней торговать.",
        ),
        item(
            "Смешанный (mixed)",
            "Часть уровня съели сделками, остальное сняли — ни то, ни другое в чистом виде.",
        ),
        item(
            "Markout, m_10s",
            "На сколько ушла средняя цена через 10 секунд после смерти уровня — это и есть \
             «сколько можно было заработать».",
        ),
        item(
            "bps",
            "Одна сотая процента: 1 bps от цены 100 долларов — это один цент.",
        ),
        item(
            "net",
            "Движение цены минус издержки круга: 7.5 bps комиссий плюс проскальзывание по \
             спреду. Положительный net — идея окупается.",
        ),
        item(
            "net_fill",
            "То же, но с поправкой на то, что заявка исполняется не всегда; считается \
             бэктестом с очередью и требует задержки круга.",
        ),
        item(
            "Задержка круга (RTT)",
            "Время от отправки заявки до подтверждения биржи; сейчас принята за 20 мс на \
             слово владельца, не измерена.",
        ),
        item(
            "p99",
            "Значение, которое хуже, чем у 99 замеров из 100 — «почти худший случай», а не \
             средний.",
        ),
        item(
            "Ставка уровней",
            "Сколько крупных уровней рождается в час — от неё зависит, наберётся ли выборка.",
        ),
        item(
            "Кластер (сутки)",
            "Единица независимого наблюдения в статистике проекта: сутки записи. Для \
             вердикта их нужно не меньше семи.",
        ),
    ]
}

// ---------------------------------------------------------------------------
// Страница
// ---------------------------------------------------------------------------

/// Страница целиком: разметка, стили и скрипт в одном файле, ни одного
/// внешнего адреса — так она открывается и с диска, и из-под
/// `python -m http.server`, и не зависит от сети.
const PAGE_TEMPLATE: &str = r##"<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>alpha — коллектор и что уже видно</title>
<style>
:root{--ink:#1c1c1e;--dim:#5f6470;--line:#e2e5ea;--bg:#f6f7f9;--card:#fff;
--ok:#127a3d;--okbg:#e8f6ed;--bad:#b3261e;--badbg:#fdecea;--neu:#5f6470;--neubg:#eef0f3;--accent:#1d4ed8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 18px 64px}
h1{font-size:25px;margin:0 0 4px}
h2{font-size:19px;margin:34px 0 4px;padding-top:16px;border-top:2px solid var(--line)}
h3{font-size:15px;margin:18px 0 6px}
p{margin:6px 0}
.sub{color:var(--dim);font-size:13px;margin:0 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.pill{display:inline-block;border-radius:999px;padding:3px 11px;font-size:12.5px;font-weight:600}
.ok{background:var(--okbg);color:var(--ok)}
.bad{background:var(--badbg);color:var(--bad)}
.neutral{background:var(--neubg);color:var(--neu)}
.big{font-size:17px;padding:7px 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:12px}
.metric{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.metric .lab{font-size:12.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.03em}
.metric .val{font-size:19px;font-weight:650;margin:3px 0 6px;word-break:break-word}
.metric .what{font-size:13px;color:var(--dim)}
.metric .thr{font-size:12px;color:var(--dim);margin-top:7px;border-top:1px dashed var(--line);padding-top:6px}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13.5px;background:var(--card)}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{background:#eef1f5;font-weight:650;text-align:right}
thead th:first-child{text-align:left}
tbody tr:hover{background:#fafbfc}
.cp{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.caveat{background:#fff8e6;border:1px solid #f0dfae;border-radius:8px;padding:10px 12px;margin-top:10px;font-size:13.5px}
.gl{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px}
.gl div{font-size:13.5px}
.gl b{color:var(--accent)}
.foot{color:var(--dim);font-size:12.5px;margin-top:26px}
.note{color:var(--dim);font-size:13px;margin-top:8px}
.kv{font-size:13px;color:var(--dim)}
.err{color:var(--bad)}
</style>
</head>
<body>
<main id="app"></main>
<script id="bootstrap" type="application/json">__BOOTSTRAP_JSON__</script>
<script>
var REFRESH = __REFRESH_SECS__;
var state = JSON.parse(document.getElementById('bootstrap').textContent);
function esc(s){return String(s===null||s===undefined?'':s)
 .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function num(v,d){return (v===null||v===undefined)?'—':Number(v).toFixed(d===undefined?2:d);}
function pct(v){return (v===null||v===undefined)?'—':(Number(v)*100).toFixed(1)+' %';}
function cls(v){return v==='ok'?'ok':(v==='bad'?'bad':'neutral');}
function vword(v){return v==='ok'?'норма':(v==='bad'?'не норма':(v==='unknown'?'нет данных':'без порога'));}
function dur(s){s=Math.max(0,Math.round(s||0));var h=Math.floor(s/3600),m=Math.floor((s%3600)/60);
 return h>0?(h+' ч '+m+' мин'):(m+' мин '+(s%60)+' с');}

function collectorBlock(d){
  var c=d.collector;
  var st=c.alive===true?'ok':(c.alive===false?'bad':'neutral');
  var word=c.alive===true?'ПИШЕТ':(c.alive===false?'НЕ ПИШЕТ':'НЕ ЗНАЮ');
  var h='<h2>1. Коллектор сейчас</h2>';
  h+='<p class="sub">Программа, которая круглосуточно записывает стакан биржи на диск. Всё остальное на странице считается из того, что она записала.</p>';
  h+='<div class="card"><span class="pill big '+st+'">'+word+'</span> ';
  h+='<span class="kv">'+esc(c.alive_reason)+'</span>';
  h+='<p class="note">Пишет с '+esc(c.started_utc)+' (UTC), это '+dur(c.recorded_secs)+' назад. ';
  h+='Последняя отметка о себе — '+esc(c.updated_utc)+'. ';
  h+='Суток в записи: '+(c.days.length||0)+'.</p>';
  h+='<p class="note">Последние '+d.tail_not_visible_secs+' секунд записи странице не видны: кадр ложится на диск целиком и не чаще, чем раз в '+d.tail_not_visible_secs+' с.</p>';
  h+='</div>';
  h+='<div class="grid">';
  for(var i=0;i<c.checks.length;i++){var k=c.checks[i];
    h+='<div class="metric"><div class="lab">'+esc(k.label)+'</div>';
    h+='<div class="val">'+esc(k.value)+'</div>';
    h+='<div class="what">'+esc(k.what)+'</div>';
    h+='<div class="thr"><span class="pill '+cls(k.verdict)+'">'+vword(k.verdict)+'</span> ';
    h+='порог: '+esc(k.threshold)+(k.source?' · '+esc(k.source):'')+'</div></div>';
  }
  h+='</div>';
  return h;
}

function checkpointsBlock(d){
  var h='<h2>2. Контрольные точки: 30 минут, 1 час, 12 часов</h2>';
  h+='<p class="sub">Промежуточные выводы по накопленному. Каждая точка считается по первым N минутам записи, поэтому три точки — три разных ответа, а не один и тот же.</p>';
  h+='<div class="cp">';
  for(var i=0;i<d.checkpoints.length;i++){var c=d.checkpoints[i];
    h+='<div class="card"><h3>'+esc(c.label)+' <span class="pill '+(c.reached?'ok':'neutral')+'">'+(c.reached?'достигнута':'не достигнута')+'</span></h3>';
    if(!c.reached){h+='<p>'+esc(c.conclusion)+'</p></div>';continue;}
    h+='<p><b>'+esc(c.conclusion)+'</b></p>';
    h+='<div class="tablewrap"><table><tbody>';
    h+='<tr><td>уровней всего</td><td>'+c.levels_total+'</td></tr>';
    h+='<tr><td>уровней в час, медианная монета</td><td>'+num(c.levels_per_hour_median,0)+'</td></tr>';
    h+='<tr><td>доля съеденных, медианная монета</td><td>'+pct(c.eaten_share_median)+'</td></tr>';
    h+='<tr><td>markout 10 с, съеденные</td><td>'+num(c.m10s_eaten_bps)+' bps</td></tr>';
    h+='<tr><td>markout 10 с, снятые</td><td>'+num(c.m10s_pulled_bps)+' bps</td></tr>';
    h+='<tr><td>markout 10 с, смешанные</td><td>'+num(c.m10s_mixed_bps)+' bps</td></tr>';
    h+='<tr><td>net после издержек ('+c.net_observations+' набл.)</td><td>'+num(c.net_bps)+' bps</td></tr>';
    h+='</tbody></table></div>';
    h+='<div class="caveat">'+esc(c.caveat)+'</div>';
    h+='</div>';
  }
  h+='</div>';
  return h;
}

function instrumentsBlock(d){
  var h='<h2>3. По инструментам</h2>';
  h+='<p class="sub">Считает сама эта страница: перечитывает записанные бинлоги тем же кодом, что и команды разметки. Порог «крупного» уровня — режим '+esc(d.h3_mode)+'.</p>';
  h+='<div class="tablewrap"><table><thead><tr>';
  var cols=['монета','записей','КБ/мин','уровней','уровней/час','съедено','снято','смешано','m 10 с, съед.','m 10 с, снят.','жизнь, медиана'];
  for(var i=0;i<cols.length;i++)h+='<th>'+cols[i]+'</th>';
  h+='</tr></thead><tbody>';
  for(var j=0;j<d.instruments.length;j++){var r=d.instruments[j];
    if(r.error){h+='<tr><td>'+esc(r.symbol)+'</td><td colspan="10" class="err">'+esc(r.error)+'</td></tr>';continue;}
    h+='<tr><td>'+esc(r.symbol)+'</td>';
    h+='<td>'+r.records+'</td><td>'+num(r.kb_per_min,1)+'</td>';
    h+='<td>'+r.levels+'</td><td>'+num(r.levels_per_hour,0)+'</td>';
    h+='<td>'+pct(r.share_eaten)+'</td><td>'+pct(r.share_pulled)+'</td><td>'+pct(r.share_mixed)+'</td>';
    h+='<td>'+num(r.m10s_eaten_bps)+'</td><td>'+num(r.m10s_pulled_bps)+'</td>';
    h+='<td>'+num(r.lifetime_median_ms,0)+' мс</td></tr>';
  }
  h+='</tbody></table></div>';
  h+='<p class="note">«Уровень» — крупная заявка на одной цене; она рождается, живёт и умирает. Столбцы «съедено / снято / смешано» делят все умершие уровни на три исхода. «m 10 с» — на сколько ушла средняя цена через десять секунд после смерти уровня, в bps.</p>';
  h+='<h3>Словарь</h3><div class="gl">';
  for(var g=0;g<d.glossary.length;g++)h+='<div><b>'+esc(d.glossary[g].title)+'</b> — '+esc(d.glossary[g].body)+'</div>';
  h+='</div>';
  return h;
}

function whereBlock(d){
  var w=d.where_we_are;
  var h='<h2>4. Где мы</h2>';
  h+='<p class="sub">Состояние проекта на '+esc(w.as_of)+'. Этот блок не считается из данных — его правит тот шаг работы, который меняет состояние.</p>';
  h+='<h3>Гейты — контрольные ворота, которые надо пройти</h3>';
  h+='<div class="tablewrap"><table><thead><tr><th>ворота</th><th>статус</th><th>что это</th></tr></thead><tbody>';
  for(var i=0;i<w.gates.length;i++){var g=w.gates[i];
    h+='<tr><td>'+esc(g.gate)+'</td><td><span class="pill '+cls(g.verdict)+'">'+esc(g.status)+'</span></td>';
    h+='<td style="text-align:left;white-space:normal">'+esc(g.what)+'</td></tr>';
  }
  h+='</tbody></table></div>';
  var sections=[['Пилот 30 минут — первые данные о рынке',w.pilot],['Аудит: пять вещей, которые надо знать',w.audit],['Что дальше',w.next]];
  for(var s=0;s<sections.length;s++){
    h+='<h3>'+sections[s][0]+'</h3><div class="card">';
    var list=sections[s][1];
    for(var k=0;k<list.length;k++)h+='<p><b>'+esc(list[k].title)+'.</b> '+esc(list[k].body)+'</p>';
    h+='</div>';
  }
  return h;
}

function render(){
  var d=state;
  var h='<h1>alpha — коллектор и что уже видно</h1>';
  h+='<p class="sub">Каталог записи: '+esc(d.root)+'. Страница пересчитана '+esc(d.generated_utc)+' и сама перечитывает данные раз в '+REFRESH+' с.</p>';
  h+=collectorBlock(d)+checkpointsBlock(d)+instrumentsBlock(d)+whereBlock(d);
  h+='<p class="foot">Все числа получены из файлов на диске тем же кодом, что и артефакты проекта. Задержка круга принята за '+d.assumed_rtt_ms+' мс на слово владельца (assumed, не измерено).</p>';
  document.getElementById('app').innerHTML=h;
}
render();
setInterval(function(){
  fetch('data.json',{cache:'no-store'}).then(function(r){return r.json();})
   .then(function(j){state=j;render();}).catch(function(){});
},REFRESH*1000);
</script>
</body>
</html>
"##;

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
    use crate::commands::lob::test_support::{snap_frame, write_day, write_day_part};

    fn write_instruments_csv(root: &Path, symbols: &[&str]) {
        let mut s = String::from(
            "symbol,turnover_usd_e9,tick_e9,step_e9,h3_lots,k,median_trade_lots,\
             window_start_utc_ms,window_secs,final_rank,selected_for_pilot\n",
        );
        for (i, sym) in symbols.iter().enumerate() {
            s.push_str(&format!(
                "{sym},1,10000000,1000000,1,1.0,1,0,3600,{},true\n",
                i + 1
            ));
        }
        std::fs::write(root.join("instruments.csv"), s).unwrap();
    }

    fn write_session_json(root: &Path, symbols: &[&str], started: &str, duration_s: u64) {
        let json = serde_json::json!({
            "started_utc": started,
            "start_hour_utc": 12,
            "duration_s": duration_s,
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
            "closed": true,
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

    /// Кадры на заданных секундах: уровень на 100 живёт от `t0` до `t1`.
    fn frames_at(minutes: &[i64]) -> Vec<Vec<crate::commands::lob::Record>> {
        let mut out = vec![snap_frame(0, &[(99, 10), (100, 10)], &[(105, 10)])];
        for (i, m) in minutes.iter().enumerate() {
            let ts = m * 60_000;
            // Каждая контрольная минута — рождение нового уровня и смерть
            // предыдущего: снимок книги без цены `100 + i`.
            #[allow(clippy::cast_possible_wrap)]
            let tick = 100 + i as i64 + 1;
            out.push(snap_frame(ts, &[(99, 10), (tick, 10)], &[(105, 10)]));
        }
        out.push(snap_frame(
            minutes.last().copied().unwrap_or(0) * 60_000 + 60_000,
            &[(99, 10)],
            &[(105, 10)],
        ));
        out
    }

    fn fixture_root(dir: &Path) -> DashboardArgs {
        write_instruments_csv(dir, &["SOLUSDT"]);
        write_session_json(dir, &["SOLUSDT"], "2026-09-12T12:00:00Z", 3600);
        DashboardArgs {
            root: dir.to_path_buf(),
            out: dir.join("out"),
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            watch: None,
        }
    }

    /// Критерий приёмки: рендер на фикстуре каталога (две части, `session.
    /// json`, `clock.csv`) даёт `data.json` с ожидаемыми полями.
    #[test]
    fn renders_data_json_with_expected_fields_on_a_two_part_fixture() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let args = fixture_root(root);
        write_day_part(root, "SOLUSDT", "2026-09-12", 1, &frames_at(&[1, 2]));
        write_day_part(root, "SOLUSDT", "2026-09-12", 2, &frames_at(&[3, 4]));
        std::fs::write(
            root.join("clock.csv"),
            "sample_index,local_ts_ns,ntp_offset_ns,ntp_rtt_ns,ntp_error,bybit_offset_ns,\
             bybit_rtt_ns,bybit_error\n0,1,7000000,2,,3,4,\n",
        )
        .unwrap();

        let summary = run_dashboard(&args).expect("рендер обязан пройти на фикстуре");
        assert!(summary.json.exists(), "data.json обязан лечь на диск");
        assert!(summary.html.exists(), "index.html обязан лечь на диск");

        let raw = std::fs::read_to_string(&summary.json).unwrap();
        let d: Dashboard = serde_json::from_str(&raw).expect("data.json обязан разбираться");
        assert_eq!(d.collector.instruments, vec!["SOLUSDT".to_string()]);
        assert_eq!(d.collector.records_total, 1000);
        assert_eq!(d.instruments.len(), 1, "строка на инструмент");
        assert!(
            d.instruments[0].error.is_none(),
            "реплей фикстуры обязан пройти: {:?}",
            d.instruments[0].error
        );
        assert!(
            d.instruments[0].levels > 0,
            "две части обязаны дать уровни, получено {}",
            d.instruments[0].levels
        );
        assert!(
            d.collector.bytes_on_disk > 0,
            "две части весят больше нуля байт"
        );
        assert_eq!(d.checkpoints.len(), CHECKPOINT_MINUTES.len());
        assert_eq!(d.assumed_rtt_ms, 20, "В-37: 20 мс, помечено assumed");
        // Порог NTP из `PLAN.md` 6.1 — 5 мс; фикстура даёт 7 мс, вердикт красный.
        let ntp = d
            .collector
            .checks
            .iter()
            .find(|c| c.label.contains("NTP"))
            .expect("проверка часов обязана быть в блоке 1");
        assert_eq!(ntp.verdict, Verdict::Bad, "7 мс > порога 5 мс: {ntp:?}");
        assert!(
            ntp.source.contains("PLAN.md 6.1"),
            "у порога обязан быть источник: {ntp:?}"
        );
    }

    /// Критерий приёмки: контрольные точки — по времени записи. Час записи
    /// достигает 30 минут и 1 часа и не достигает 12 часов.
    #[test]
    fn checkpoints_are_reached_by_recorded_time_only() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let args = fixture_root(root);
        write_day(root, "SOLUSDT", "2026-09-12", &frames_at(&[1, 2, 3]));

        let d = build_dashboard(&args).unwrap();
        let by_minutes = |m: u32| {
            d.checkpoints
                .iter()
                .find(|c| c.minutes == m)
                .unwrap_or_else(|| panic!("контрольная точка {m} обязана быть"))
        };
        assert!(by_minutes(30).reached, "час записи покрывает 30 минут");
        assert!(by_minutes(60).reached, "час записи покрывает 1 час");
        assert!(
            !by_minutes(720).reached,
            "часа записи не хватает на 12 часов"
        );
        assert!(
            by_minutes(720).conclusion.contains("осталось"),
            "недостигнутая точка обязана сказать, сколько осталось: {}",
            by_minutes(720).conclusion
        );
        assert!(
            by_minutes(30).caveat.contains(&G_MIN.to_string()),
            "оговорка В-38 обязана назвать G_MIN текстом: {}",
            by_minutes(30).caveat
        );
    }

    /// Контрольная точка режет выборку по времени записи, а не берёт всё:
    /// уровень, родившийся на 45-й минуте, в тридцатиминутную точку не
    /// входит, а в часовую входит.
    #[test]
    fn a_level_born_after_the_checkpoint_is_not_counted_in_it() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let args = fixture_root(root);
        write_day(root, "SOLUSDT", "2026-09-12", &frames_at(&[10, 45]));

        let d = build_dashboard(&args).unwrap();
        let at30 = d.checkpoints.iter().find(|c| c.minutes == 30).unwrap();
        let at60 = d.checkpoints.iter().find(|c| c.minutes == 60).unwrap();
        assert!(
            at30.levels_total < at60.levels_total,
            "на 30 минутах уровней обязано быть меньше, чем на часе: {} против {}",
            at30.levels_total,
            at60.levels_total
        );
    }

    /// Критерий приёмки: страница — валидный самодостаточный HTML без единого
    /// внешнего адреса (CSP/офлайн: открывается и с файла, и с localhost).
    #[test]
    fn page_is_self_contained_html_without_external_urls() {
        let html = render_html("{\"ok\":true}");
        for tag in ["html", "head", "body", "style", "script"] {
            let open = html.matches(&format!("<{tag}")).count();
            let close = html.matches(&format!("</{tag}>")).count();
            assert!(open > 0, "тег <{tag}> обязан быть на странице");
            assert_eq!(open, close, "теги <{tag}> обязаны быть закрыты");
        }
        assert!(html.starts_with("<!doctype html>"), "нужен доктайп");
        for external in ["http://", "https://", "src=\"//", "@import"] {
            assert!(
                !html.contains(external),
                "внешних адресов на странице быть не должно, найден {external}"
            );
        }
        assert!(
            html.contains("data.json"),
            "страница обязана перечитывать data.json"
        );
        assert!(
            html.contains(&PAGE_REFRESH_SECS.to_string()),
            "период самообновления обязан подставиться"
        );
    }

    /// JSON с `</script>` внутри не разрывает тег — иначе страница ломается
    /// на первом же сообщении об ошибке, содержащем разметку.
    #[test]
    fn bootstrap_json_cannot_close_the_script_tag() {
        let html = render_html("{\"e\":\"</script><b>\"}");
        assert!(
            !html.contains("</script><b>"),
            "закрывающий тег из данных обязан быть экранирован"
        );
    }

    /// Каталог без `session.json` — не каталог записи; ошибка называет файл.
    #[test]
    fn missing_session_json_names_the_file() {
        let dir = tempfile::tempdir().unwrap();
        let args = DashboardArgs {
            root: dir.path().to_path_buf(),
            out: dir.path().join("out"),
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            watch: None,
        };
        let err = build_dashboard(&args).unwrap_err().to_string();
        assert!(err.contains("session.json"), "{err}");
    }

    /// Живость: закрытая сессия — «остановлен»; открытая без предыдущего
    /// расчёта — «пока не знаю»; открытая с ростом байтов — «жив».
    #[test]
    fn liveness_needs_growth_between_two_renders_not_just_the_closed_flag() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let args = fixture_root(root);
        write_day(root, "SOLUSDT", "2026-09-12", &frames_at(&[1]));

        let d = build_dashboard(&args).unwrap();
        assert_eq!(d.collector.alive, Some(false), "closed=true — остановлен");
        assert!(d.collector.alive_reason.contains("остановлен"));

        // Тот же каталог, но сессия не закрыта и свежая: первый расчёт не
        // знает ответа, потому что сравнить размер бинлогов не с чем.
        let raw = std::fs::read_to_string(root.join("session.json")).unwrap();
        let mut v: serde_json::Value = serde_json::from_str(&raw).unwrap();
        v["closed"] = serde_json::Value::Bool(false);
        v["updated_utc"] = serde_json::Value::String(chrono::Utc::now().to_rfc3339());
        std::fs::write(root.join("session.json"), v.to_string()).unwrap();
        let d = build_dashboard(&args).unwrap();
        assert_eq!(d.collector.alive, None, "первый расчёт не знает: {d:?}");
        assert!(d.collector.alive_reason.contains("не с чем сравнить"));
    }
}
