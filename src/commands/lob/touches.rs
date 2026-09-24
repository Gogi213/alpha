//! `lob touches` — CLI-обёртка касаний живых уровней (таск 35, В-42).
//! Сам учёт касаний — `crate::lob::levels` (`TouchRecord`,
//! `LevelTracker::observe_frame_with_touches`), markout и подход —
//! `crate::lob::markout` (`markouts_for_touch`, `approaches_for_touch`);
//! реплей суточных файлов — общий с `levels`/`markout` `super::replay_symbol`
//! (`ReplayDay.touches`). Здесь только флаги, CSV и код выхода.
//!
//! Колонки: запись касания как есть (`birth_ms` — рождение уровня, как в
//! `levels-*.csv`) плюс `age_ms` (возраст уровня на момент касания),
//! `dist_bps` (расстояние уровня до середины при его рождении — один расчёт с
//! осью `distance` у `profiles`, `markout::distance_bps_at_birth`), markout на
//! четырёх горизонтах `HORIZONS_MS` от среза как есть на `start_ms` (В-43) со
//! знаком «в сторону отскока», подход за `APPROACH_MS` (1 с, 10 с) со знаком
//! «к уровню». Пустая ячейка — нет данных (нет среза на момент касания или
//! на горизонте), не ноль. `frontrun_lots` — за секунду до касания,
//! `swept_lots` — на последнем кадре до него (В-45); `within_touch_<h>` —
//! горизонт не длиннее касания (`markout::within_touch`): `m_<h>` там ≈ 0
//! по построению, читателю средних такую ячейку надо пропустить.
//!
//! Флаг `--approach-bps D` (F1 этапа F, В-73) добавляет рядом
//! `approaches-<SYMBOL>.csv`: моменты, когда цена подошла к живому уровню на
//! `D` bps **до** касания (`ApproachRecord` трекера, правила — в
//! `crate::lob::levels`, «Подход»). Колонки — поля записи плюс `day_utc`,
//! `age_ms` и `duration_ms`; `touch_start_ms` пустой, если подход кончился не
//! касанием. Без флага ни файла, ни состояния подхода в трекере: касания —
//! те же байты. Обратное чтение — `read_approaches_csv`.
//!
//! Тело раньше жило одной функцией на ~434 строки и тринадцать разных задач
//! (W5в); теперь по подмодулям — каждый один шаг конвейера:
//! - `plan` — разбор аргументов и ранняя валидация (W5а): всё, что можно
//!   проверить без единого байта реплея, проверяется до него;
//! - `row` — строка `touches-<SYMBOL>.csv`/`approaches-<SYMBOL>.csv`, пара
//!   «имя колонки → значение» на позицию (W5г);
//! - `moves` — переезды плотностей `--moves` (T42, В-46);
//! - `numbers` — числа практиков `--numbers` (T42);
//! - `read` — обратное чтение CSV в записи трекера, общий помощник полей
//!   (было продублировано в `TouchCols`/`ApproachCols`, W5в).

use std::path::PathBuf;

use clap::Args;

use crate::lob::markout::{mid_double_tick, raw_return_bps, sample_asof};

use super::{H3Args, ReplayKeep, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS};

mod moves;
mod numbers;
mod plan;
mod read;
mod row;

pub(crate) use read::{read_approaches_csv, read_touches_csv, ApproachRow, TouchRow};

/// Аргументы `lob touches`: читает суточные файлы, пишет касания живых
/// уровней с признаками практиков. Режим `H3` — без умолчания, как у
/// `levels`; `--h3-k` — тот же пересчёт пола на лету (таск 18).
#[derive(Debug, Args)]
pub struct TouchesArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Режим `H3`: `--h3-mode floor|percentile|notional|strength|both` с флагами
    /// режима (`--h3-lots`, `--h3-usd`, `--h3-strength-pct`, `--h3-strength-window-bps`;
    /// В-61) — общие для нескольких подкоманд.
    #[command(flatten)]
    pub h3: H3Args,
    /// Множитель относительного порога `floor` (таск 18, В-30/D05):
    /// `h3_lots = floor(k × median_trade_lots)` из `instruments.csv`.
    /// Только `--h3-mode floor`.
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Прогрев в мс: только режим `percentile`; `floor` не читает.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс (нужно трекеру, в касания не пишется).
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать касания (по умолчанию `<root>/touches-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Полоса сигнала **подхода** `D` в bps (F1 этапа F, В-73): рядом с
    /// касаниями пишется `approaches-<symbol>.csv` — моменты, когда цена
    /// другой стороны подошла к живому уровню на `D` bps. Без флага записей
    /// подхода нет вовсе (байты касаний прежние).
    ///
    /// **Список значений** (`--approach-bps 10,20,30,50`, владелец 20.09:
    /// в предрегистрацию идут несколько полос) — один реплей, по трекеру на
    /// полосу: первая пишет привычный `approaches-<symbol>.csv`, каждая
    /// следующая — `approaches-<symbol>-D<d>.csv`. Так нельзя сложить полосы
    /// в один файл: момент взвода у разных `D` разный (цена пересекает
    /// широкую полосу раньше узкой), и запись с `D = 20` не даёт корректного
    /// взвода на 50.
    #[arg(long, value_delimiter = ',')]
    pub approach_bps: Vec<i64>,
    /// Минимальный возраст уровня к моменту взвода подхода, секунды (флор
    /// В-71 — 900 с; без флага пола нет). Только вместе с `--approach-bps`.
    #[arg(long, default_value_t = 0)]
    pub approach_min_age_secs: i64,
    /// Заодно измерить **переезды плотностей** (T42, В-46) и записать пары
    /// «смерть → рождение» в этот CSV. Метка `moved` не ставится: измеряется
    /// распределение, границы назначаются отдельным решением.
    #[arg(long)]
    pub moves: Option<PathBuf>,
    /// Окно поиска рождения для `--moves`, мс. Без умолчания: это параметр
    /// измерения, а не константа кода (В-46), и он печатается в шапке.
    #[arg(long)]
    pub moves_window_ms: Option<i64>,
    /// Шаг гистограммы `dt_ms` для `--moves`, мс (без него гистограмма не
    /// печатается: шаг — параметр, а не назначенное число).
    #[arg(long)]
    pub moves_bin_ms: Option<f64>,
    /// Выгрузить **числа практиков** (T42): квантили p10/p50/p90 размера (лоты
    /// / ×H3 / $), расстояния (bps) и времени жизни — по касаниям и по умершим
    /// уровням, отдельно по исходам и по осям В-44.
    #[arg(long)]
    pub numbers: Option<PathBuf>,
    /// Снять требование маркера сверки `verify-<SYMBOL>.status == ok` (отладочные
    /// данные; К1 аудита 18.09).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
    /// Переносить возраст живых уровней через смежную полночь (аудит дизайна 22.09 Т3): без
    /// флага трекер стартует с нуля в 00:00 UTC — прежние байты. Только режимы без прогрева
    /// (`notional`/`strength`/`both`/`floor`).
    #[arg(long, default_value_t = false)]
    pub carry_age: bool,
    /// Писать записи только этих суток (`YYYY-MM-DD`): корень может нести и прошлые сутки —
    /// для переноса возраста (`--carry-age`), — но кэш суток остаётся кэшем одних суток.
    #[arg(long)]
    pub emit_day: Option<String>,
}

/// Итог `lob touches` для печати диспетчером.
#[derive(Debug)]
pub struct TouchesSummary {
    pub days: usize,
    pub touches: usize,
    /// Записей подхода (F1) — ноль, если `--approach-bps` не задан.
    pub approaches: usize,
    pub out: PathBuf,
    /// Файлы записей подхода, если они писались: первый — привычное имя
    /// `approaches-<symbol>.csv`, дальше по полосе на файл (`-D<d>`).
    pub approaches_out: Vec<PathBuf>,
}

/// Ширина строки CSV — один источник арности для заголовка и строки:
/// расхождение не компилируется.
const TOUCHES_WIDTH: usize = 61;

/// Ширина строки `approaches-<SYMBOL>.csv` (F1) — как `TOUCHES_WIDTH`.
const APPROACHES_WIDTH: usize = 19;

/// Заголовок `approaches-<SYMBOL>.csv`: поля `ApproachRecord` плюс `day_utc`,
/// `age_ms` и `duration_ms` (производные, как у касаний; `touch_start_ms`
/// пустой, если подход кончился не касанием). Полоса взвода `D` и потолок
/// гистерезиса `2·D` — параметры прогона: их печатает манифест шага F10,
/// в шапке строки они не дублируются.
pub(crate) const APPROACHES_COLUMNS: [&str; APPROACHES_WIDTH] = [
    "day_utc",
    "side",
    "price_tick",
    "approach_index",
    "arm_ms",
    "age_ms",
    "arm_dist_bps",
    "birth_ms",
    "size_at_arm",
    "best_own_tick",
    "best_opp_tick",
    "flow_1h_lots",
    "strength_w10_pct",
    "strength_w20_pct",
    "strength_w50_pct",
    "touch_start_ms",
    "disarm_ms",
    "duration_ms",
    "disarm_reason",
];

/// Заголовок CSV: запись касания как есть, затем производные. `birth_ms` —
/// как в `levels-*.csv`/`markout-*.csv`, для джойна по (сторона, тик,
/// рождение).
pub(crate) const TOUCHES_COLUMNS: [&str; TOUCHES_WIDTH] = [
    "day_utc",
    "side",
    "price_tick",
    "touch_index",
    "start_ms",
    "end_ms",
    "duration_ms",
    "birth_ms",
    "age_ms",
    "size_at_touch",
    "size_max_before",
    "traded_during",
    "frontrun_lots",
    "frontrun_tick",
    "swept_lots",
    "round_zeros",
    "ended_by_death",
    "stack_levels",
    "dist_bps",
    "m_100ms",
    "m_1s",
    "m_10s",
    "m_60s",
    "within_touch_100ms",
    "within_touch_1s",
    "within_touch_10s",
    "within_touch_60s",
    "approach_1s",
    "approach_10s",
    // Длинные горизонты (B3, В-58 п. 4): 10 мин / 1 ч / 2 ч — «от секунд до,
    // наверно, пары часов» (ответ владельца 2). Отдельные колонки, а не
    // продолжение `m_*`: у тех есть парные `within_touch_*`, у этих — нет.
    "m_10m",
    "m_1h",
    "m_2h",
    // Ось силы «×соседи» на кадре старта касания (исследование порога В-61,
    // 2026-09-18): проценты от среднего соседа той же стороны в ±10/20/50 bps;
    // пусто — соседей в окне не было.
    "strength_w10_pct",
    "strength_w20_pct",
    "strength_w50_pct",
    // История силы (владелец 2026-09-18): минимум силы ±20 bps за последние
    // 1/5/15/60 с по секундным выборкам; пусто — уровень моложе окна.
    "strength_held_1s_pct",
    "strength_held_5s_pct",
    "strength_held_15s_pct",
    "strength_held_60s_pct",
    // Прошлых рождений на этой цене за час до касания (мерцание).
    "repeat_count",
    // `σ_H` середины (В-62, `lob::sigma`): реализованная волатильность в bps за
    // окно, равное дедлайну сетки (`DEADLINE_SECS`), к началу касания; пусто —
    // окно упирается в начало записи.
    "sigma_60s_bps",
    "sigma_600s_bps",
    "sigma_3600s_bps",
    "sigma_7200s_bps",
    // Экскурсия после касания (`lob::excursion`, замер `a`/`b` В-62): самый
    // большой ход середины против отскока и за отскок внутри дедлайна, bps от
    // базы касания, по секундным срезам; пусто — окно за концом записи.
    "adverse_60s_bps",
    "adverse_600s_bps",
    "adverse_3600s_bps",
    "adverse_7200s_bps",
    "favour_60s_bps",
    "favour_600s_bps",
    "favour_3600s_bps",
    "favour_7200s_bps",
    // База отскока (В-64): вторая плотность завала — тик ближайшей живой
    // плотности за уровнем в окне стека (E6, стоп «за первую-вторую»); объём
    // против уровня за первые 1/2/3 с касания (E4, окно реакции), лоты.
    "stack_next_tick",
    "traded_1s",
    "traded_2s",
    "traded_3s",
    // Сила «×поток»: оборот инструмента за час к касанию (лоты) и размер
    // уровня в процентах от него (пусто — оборота за час не было).
    "flow_1h_lots",
    "strength_flow_pct",
    // Ход середины **до** касания (S2 плана по сторонам, 2026-09-20): от среза
    // за `PRE_TOUCH_MS` до касания к базе касания, bps, знак абсолютный (плюс
    // — цена выросла к касанию; «растяжка» [T 1:05:21], направление [D 10:17]);
    // пусто — записи столько нет (трекер суточный: первые 4 ч суток без
    // `ret_4h` — систематика, не пропуск).
    "ret_10m_bps",
    "ret_1h_bps",
    "ret_4h_bps",
];

/// Окна хода до касания: 10 мин / 1 ч / 4 ч — из цитат практиков про
/// «направление за час / за четыре часа», не новое число.
pub const PRE_TOUCH_MS: [i64; 3] = [600_000, 3_600_000, 14_400_000];

/// Сравнение массивов в константном контексте (W5б): `debug_assert_eq!` не
/// выполняется в `--release`, а `cargo test --release --target-dir target-ci`
/// (`COMMANDS.md`) — ровно тот профиль, которым гоняются тесты проекта, так
/// что три проверки ниже раньше не выполнялись вовсе, ни в одном настоящем
/// прогоне. `const _: () = assert!(...)` проверяется компилятором всегда,
/// для любого профиля, и генерик по `N` делает несовпадение ДЛИНЫ массивов
/// ошибкой типов, а не молчаливым `false`. Два помощника, не один общий по
/// элементу: `DEADLINE_SECS` — `[u64; _]`, `HORIZONS_MS`/`APPROACH_MS` —
/// `[i64; _]`, а сравнение по обобщённому элементу в `const fn` без
/// нестабильных константных трейтов не собирается.
const fn i64_eq<const N: usize>(a: [i64; N], b: [i64; N]) -> bool {
    let mut i = 0;
    while i < N {
        if a[i] != b[i] {
            return false;
        }
        i += 1;
    }
    true
}

const fn u64_eq<const N: usize>(a: [u64; N], b: [u64; N]) -> bool {
    let mut i = 0;
    while i < N {
        if a[i] != b[i] {
            return false;
        }
        i += 1;
    }
    true
}

const _: () = assert!(
    i64_eq(
        crate::lob::markout::HORIZONS_MS,
        [100, 1_000, 10_000, 60_000]
    ),
    "порядок колонок m_* обязан совпадать с горизонтами"
);
const _: () = assert!(
    i64_eq(crate::lob::markout::APPROACH_MS, [1_000, 10_000]),
    "порядок колонок approach_* обязан совпадать с окнами подхода"
);
const _: () = assert!(
    u64_eq(
        super::bounce_verdict::DEADLINE_SECS,
        [60, 600, 3_600, 7_200]
    ),
    "порядок колонок sigma_* обязан совпадать с окнами дедлайнов"
);

/// Ход середины до касания: база касания против последнего среза не позже
/// `start_ms − pre_ms`; `None` — среза нет (начало записи ближе окна).
pub fn pre_touch_return_bps(
    mids: &[crate::lob::markout::MidSample],
    start_ms: i64,
    pre_ms: i64,
) -> Option<f64> {
    let (_, base2x) = crate::lob::markout::touch_base(mids, start_ms)?;
    let before = sample_asof(mids, start_ms, -pre_ms)?;
    raw_return_bps(mid_double_tick(before.bid_tick, before.ask_tick), base2x)
}

/// То же, что пишет CSV (`{:.6}`): каноническое значение хода до касания —
/// одно и то же из кэша и из реплея (`bounce_grid` сравнивает его с
/// порогами наборов, поэтому байты обязаны совпадать).
pub fn pre_touch_return_bps_csv(
    mids: &[crate::lob::markout::MidSample],
    start_ms: i64,
    pre_ms: i64,
) -> Option<f64> {
    pre_touch_return_bps(mids, start_ms, pre_ms).and_then(|v| format!("{v:.6}").parse().ok())
}

/// Имя минутного ряда середины рядом с `touches-<SYMBOL>.csv`.
pub fn mids1m_path(touches_out: &std::path::Path, symbol: &str) -> PathBuf {
    touches_out.with_file_name(format!("mids1m-{symbol}.csv"))
}

/// Минутный ряд середины символа (S2/S3 плана по сторонам): на каждую минуту
/// от первого до последнего среза суток — `minute_ms` (начало минуты, unix
/// мс) и `mid2x` последнего среза **внутри** минуты (закрытие); минута без
/// срезов несёт предыдущее закрытие. Режим пула (`regime.py`) читает эти файлы.
fn write_mids1m(
    path: &std::path::Path,
    days: &[super::replay::ReplayDay],
) -> anyhow::Result<usize> {
    let mut w = csv::Writer::from_path(path)?;
    w.write_record(["minute_ms", "mid2x"])?;
    let mut n = 0usize;
    for day in days {
        let (Some(first), Some(last)) = (day.mids.first(), day.mids.last()) else {
            continue;
        };
        let mut i = 0usize;
        let mut close: Option<i64> = None;
        let mut minute = first.ts_ms.div_euclid(60_000);
        let last_minute = last.ts_ms.div_euclid(60_000);
        while minute <= last_minute {
            let end = (minute + 1) * 60_000;
            while i < day.mids.len() && day.mids[i].ts_ms < end {
                close = Some(mid_double_tick(day.mids[i].bid_tick, day.mids[i].ask_tick));
                i += 1;
            }
            if let Some(c) = close {
                w.write_record([(minute * 60_000).to_string(), c.to_string()])?;
                n += 1;
            }
            minute += 1;
        }
    }
    w.flush()?;
    Ok(n)
}

/// Реплей символа тем же `replay_symbol`, что `levels`/`markout`, и запись
/// касаний в CSV. Порядок строк — порядок выдачи трекера внутри суток.
pub fn run_touches(args: &TouchesArgs) -> anyhow::Result<TouchesSummary> {
    // Вся проверка аргументов и режима — до единого байта реплея (W5а):
    // `plan::resolve` отказывает на плохом `--moves-window-ms` или на
    // `--numbers` с режимом без единого H3 сразу, не после дорогого реплея.
    let plan::ResolvedPlan {
        bands,
        cfg,
        h3_lots,
    } = plan::resolve(args)?;

    // Несколько полос — один реплей и по трекеру на полосу (`ReplayStats` на
    // конфигурацию); касания одинаковы у всех (сигнал подхода их не меняет),
    // поэтому берём раскладку первой полосы, а записи подхода — у каждой своей.
    let mut replays = if bands.len() > 1 {
        let cfgs: Vec<crate::lob::levels::LevelsConfig> = bands
            .iter()
            .map(|&d| crate::lob::levels::LevelsConfig {
                approach_bps: Some(d),
                ..cfg
            })
            .collect();
        let mut stats = super::replay_symbol_over_configs_keep(
            &args.root,
            &args.symbol,
            &cfgs,
            ReplayKeep::ALL.with_carry_age(args.carry_age),
        )?;
        anyhow::ensure!(
            stats.len() == cfgs.len(),
            "реплей вернул {} раскладок на {} конфигураций",
            stats.len(),
            cfgs.len()
        );
        std::mem::take(&mut stats)
    } else {
        super::replay_symbol_over_configs_keep(
            &args.root,
            &args.symbol,
            std::slice::from_ref(&cfg),
            ReplayKeep::ALL.with_carry_age(args.carry_age),
        )?
    };
    // Кэш одних суток из корня с прошлыми сутками (перенос возраста): остальные сутки —
    // только прогрев трекера, в файлы не идут.
    if let Some(d) = &args.emit_day {
        for r in &mut replays {
            r.days.retain(|x| &x.day == d);
        }
    }
    // W5в: `remove(0)` без проверки длины паниковал бы вместо чистого отказа,
    // если бы реплей вернул пустую раскладку (контракт `replay_symbol_over_configs_keep`
    // это не гарантирует явно для одной конфигурации).
    anyhow::ensure!(!replays.is_empty(), "реплей не вернул ни одной раскладки");
    let replay = replays.remove(0);
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("touches-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(TOUCHES_COLUMNS)?;
    let mut n = 0usize;
    // Записи подхода (F1) — рядом с касаниями, тем же прогоном: трекеры уже
    // их ведут, отдельного реплея не нужно. Файл на полосу: первая — привычное
    // имя `approaches-<symbol>.csv`, остальные — `approaches-<symbol>-D<d>.csv`.
    let approaches_out: Option<Vec<PathBuf>> = if bands.is_empty() {
        None
    } else {
        Some(
            bands
                .iter()
                .enumerate()
                .map(|(k, d)| {
                    let name = if k == 0 {
                        format!("approaches-{}.csv", args.symbol)
                    } else {
                        format!("approaches-{}-D{}.csv", args.symbol, d)
                    };
                    match out.parent() {
                        Some(p) if !p.as_os_str().is_empty() => p.join(name),
                        _ => PathBuf::from(name),
                    }
                })
                .collect(),
        )
    };
    let mut wa: Vec<csv::Writer<std::fs::File>> = Vec::new();
    if let Some(paths) = &approaches_out {
        for path in paths {
            let mut w = csv::Writer::from_path(path)?;
            w.write_record(APPROACHES_COLUMNS)?;
            wa.push(w);
        }
    }
    let mut n_ap = 0usize;
    // Ряд `σ` — по всей записи символа (В-62): окно раннего касания вторых
    // суток смотрит в первые.
    let (sigma_series, second_mids) = {
        let mut all: Vec<crate::lob::markout::MidSample> = Vec::new();
        for day in &replay.days {
            all.extend(day.mids.iter().copied());
        }
        (
            crate::lob::sigma::SigmaSeries::from_mids(&all),
            crate::lob::excursion::SecondMids::from_mids(&all),
        )
    };
    for (day_i, day) in replay.days.iter().enumerate() {
        for t in &day.touches {
            let row = row::touch_row(day, t, &sigma_series, &second_mids);
            w.write_record(row)?;
            n += 1;
        }
        // Записи подхода этих суток (порядок — порядок трекера, детерминирован):
        // по полосе на файл, у каждой полосы — свой трекер и своя раскладка суток.
        for (band_i, writer) in wa.iter_mut().enumerate() {
            let stats = if band_i == 0 {
                &replay
            } else {
                &replays[band_i - 1]
            };
            let Some(band_day) = stats.days.get(day_i) else {
                continue;
            };
            for a in &band_day.approaches {
                let row = row::approach_row(&band_day.day, a);
                writer.write_record(row)?;
                n_ap += 1;
            }
        }
    }
    w.flush()?;
    for writer in wa.iter_mut() {
        writer.flush()?;
    }
    // Минутный ряд середины — рядом с касаниями (S3: режим пула по минутам).
    write_mids1m(&mids1m_path(&out, &args.symbol), &replay.days)?;

    // Переезды плотностей (T42, В-46) — отдельным файлом: окно поиска
    // проверено `plan::resolve` (W5а), здесь оно уже гарантированно задано и
    // положительно.
    if let Some(path) = &args.moves {
        let window_ms = args
            .moves_window_ms
            .expect("plan::resolve проверил --moves-window-ms");
        moves::run_moves(
            &replay.days,
            window_ms,
            args.moves_bin_ms,
            &args.symbol,
            path,
        )?;
    }

    if args.numbers.is_some() {
        let h3_lots = h3_lots.expect("plan::resolve проверил h3_lots для --numbers");
        numbers::write_numbers(args, &replay, h3_lots, replay.tick_e9, replay.step_e9)?;
    }

    Ok(TouchesSummary {
        days: replay.days.len(),
        touches: n,
        approaches: n_ap,
        out,
        approaches_out: approaches_out.unwrap_or_default(),
    })
}

#[cfg(test)]
mod tests;
