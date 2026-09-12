//! `lob profiles` — первый из трёх артефактов задачи (таск 10, история 44):
//! `docs/findings/profiles-<дата>.csv`, строка на каждый профиль сетки
//! `shortlist::build_profile_grid` (Decision 26/26а), **полная**: профили с
//! плохим результатом и с недобором `n` остаются, непригодная корзина
//! отсутствует вовсе (её попросту нет среди ключей сетки), а не печатается
//! нулём.
//!
//! # Вход
//!
//! `--root` — каталог с `instruments.csv` (пул, `lob pick`) и подкаталогом на
//! каждую сессию (`lob session`, таск 04): `session.json`,
//! `<SYMBOL>-<день>[-pN].binlog` (таск 19/22, резолвер
//! `super::session_parts_for` — с таска 23 сутки и час старта у каждой
//! части свои, каталог с частями за D и D+1 даёт два кластера суток),
//! маркер сверки `verify-<SYMBOL>.status` (таск 07/09). По умолчанию сессия
//! без маркера `ok` пропускается целиком (fail-closed, тот же приём, что у
//! `commands::lob::watch`); `--allow-unverified` снимает это требование для
//! отладочных данных без маркеров (`data/session-debug/…`) и обязан нести
//! метку `debug` в шапке файла — это ветка «данными не является», критерий
//! приёмки не путает её с боевым прогоном.
//!
//! `--candidates-csv` (по умолчанию `docs/plan/candidates.csv`, таск 08) —
//! источник `coverage_top50_bps`: `instruments.csv` корня несёт только пул и
//! пол `H3`, покрытие книги остаётся в полной таблице кандидатов
//! (`interfaces.md`, «Из таска 08»). Без строки покрытия для символа пула —
//! громкая ошибка, не молчаливое исключение.
//!
//! # Оси и разметка
//!
//! Разметка уровня — `lob::levels` (шесть признаков, исход по 70/20),
//! markout — `lob::markout` (`markouts_for_level`, знак по стороне),
//! издержки — `lob::costs`. Расстояние до середины и размер относительно
//! `H3` в этой библиотеке нигде не посчитаны (`LevelRecord` их не хранит) —
//! оба считаются здесь: расстояние — `markout::raw_return_bps` между ценой
//! уровня и серединой на срезе строго до рождения (тот же приём, что
//! `markout::base_before` для смерти, сдвинутый на `birth_ms + 1`), размер —
//! отношение `size_max` к порогу `H3`, применённому при рождении. Числовые
//! границы корзин размера и времени жизни (`SIZE_BOUNDS`,
//! `LIFETIME_BOUNDS_MS` ниже) — те же числа, что `shortlist::SIZE_LABELS`/
//! `LIFETIME_LABELS` (и приложенная спека «Профиль», история 14): у
//! расстояния есть парная числовая таблица (`DISTANCE_BOUNDS_BPS`), у этих
//! двух осей — нет, тест `size_and_lifetime_bounds_match_shortlist_labels`
//! ниже держит числа и метки в одном месте.
//!
//! # `m` с интервалом и `net_fill` — один и тот же приём (таск 09, `pilot.rs`)
//!
//! Интервал на `m` по каждому горизонту — тот же совместный бутстрап, что
//! `net_fill` (`costs::net_fill_interval`), с `filled = true` на каждом
//! наблюдении: вторая серия вырождается в константу, и совместный ресэмплинг
//! сводится к обычному Уэббовскому интервалу среднего одной серии —
//! расширению `disjoint_contrast`, а не второму бутстрапу
//! (`interfaces.md`, «Из таска 03»: «второй ресэмплер не писать»). Тот же
//! приём уже принят `commands::lob::pilot::process_instrument` для `m_10s`;
//! здесь он один и тот же на всех четырёх горизонтах. `alpha`/`replications`
//! — `stats::GATE_ALPHA`/`stats::BOOTSTRAP_REPLICATIONS`, `seed = 0` — то же
//! число, с которым уже вызывает `net_fill_interval` боевой пилот: не
//! изобретённое здесь, а то же самое, напечатанное в шапке файла.
//!
//! # `net`/`fill`/`net_fill` — и почему `fill*` не печатает число сегодня
//!
//! Считаются на горизонте вердиктной ячейки (10 с, `HORIZONS_MS[2]`) — тот
//! же горизонт, что `pilot.rs::process_instrument` берёт для `net`/Шарпа.
//! `net` — средний `m` за вычетом издержек, без веса на исполнение, поэтому
//! печатается всегда, когда есть наблюдения.
//!
//! `fill`/`net_fill`/`net_fill_lower` — другая история: исполнился ли вход
//! за отведённые 2 с, нигде в дереве не измерено (`interfaces.md`, «Из
//! таска 03»: «`filled` не различает…» — задача таска 11, `lob backtest` с
//! `RiskAdverseQueueModel`). Ревью таска 10 (BLOCKING, ось Манифест) поймало
//! более раннюю редакцию этого файла на плейсхолдере `filled = true` для
//! каждого наблюдения: число тогда получалось правдоподобным (`fill=1.0000`,
//! `net_fill == net`), а «`net_fill` — вот это и есть окупается» решает
//! судьбу профиля — правдоподобное число, посчитанное без модели исполнения,
//! есть тот самый выдуманный факт, который запрещён §9. Поэтому вход —
//! трейт `FillModel` (см. `run_profiles_with_fill_model`, `NoFillModel`
//! ниже): без реальной модели (сегодня, до таска 11) он не судит ни об одном
//! наблюдении, `fill_obs` профиля остаётся пуст, и `write_row` печатает
//! литерал `not_measured` в этих трёх колонках, а шапка несёт
//! `fill_model=none`. Когда таск 11/12/13 подключит бэктест, вызывающий
//! передаст `run_profiles_with_fill_model` реализацию `FillModel` поверх
//! `lob::backtest`, шапка сменит метку на `fill_model=backtest`, и те же три
//! колонки начнут печатать настоящие числа — сама формула (`costs::
//! fill_rate`/`net_fill_bps`/`net_fill_interval`) не меняется.
//!
//! # Флаг `unreachable` (история 15, гейт G-LAT)
//!
//! `lob react` (таск 15) не пишет персистентный отчёт с горизонтами
//! (`ReactReport`/`HorizonReach` — только `Debug`/`Vec` в памяти дispatcher'а,
//! файл на диске — сырые срезы `react-<SYMBOL>.csv`, без публичного ридера,
//! который восстановил бы `HorizonReach` без дублирования приватной логики
//! `react.rs` — модуль вне зоны этого таска). Ни один живой отчёт G-LAT ещё
//! не существует (`interfaces.md`, «Из таска 15»: «чисел G-LAT… нет»).
//! Колонки `unreachable_<horizon>` печатают `not_measured` буквально по
//! критерию приёмки таска («не пусто и не false») — не по недосмотру:
//! CONCERNS.
//!
//! # `runs.csv`
//!
//! Боевой режим (без `--allow-unverified`) пишет каждый id сетки одной
//! строкой `runs.csv` (`shortlist::log_profile_trials`) — испытание, как и
//! любой посчитанный профиль (спека «Профиль», R29/R46). Отладочный режим
//! файл не трогает (история 13, тот же принцип, что `pilot --debug`).
//!
//! Тест на час суток (`shortlist::hour_dependence_test`, ремонт по ревью
//! таска 12, открытый пункт (2) для таска 13) идёт в тот же журнал одной
//! строкой на профиль (`shortlist::log_hour_test`) — на горизонте
//! вердиктной ячейки (10 с), кластер — сутки, значение — среднее `m_10s` за
//! эти сутки, час — средний час старта сессий этих суток
//! (`ProfileAgg::hour_day_sums`). Методический отказ теста (суток меньше
//! `G_MIN`, сетка Уэбба грубее альфы, наблюдаемая вырождена) не пишется:
//! как и непригодная корзина сетки, такое испытание не состоялось и не
//! входит в `total_trials`.
//!
//! `G` (годных суток на профиль, ремонт по ревью таска 12, открытый пункт
//! (1) для таска 13) — новая колонка `g` в конце шапки
//! (`ProfileAgg::days`): число различных суток, на которые пришлось хотя бы
//! одно наблюдение профиля. `commands::lob::shortlist` берёт её вместо
//! прежнего захардкоженного нуля для `decide_profile`/подтверждающей шапки.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog::Reader;
use crate::book::Book;
use crate::bybit::verify::FileReplayer;
use crate::lob::costs::{
    fill_rate, format_fill_column, mean_net_bps, net_bps as cost_net_bps, net_fill_bps,
    net_fill_interval, FillObservation, Observation,
};
use crate::lob::final_metrics::sharpe_ratio;
use crate::lob::levels::{H3Mode, LevelRecord, LevelTracker, LevelsConfig, Outcome};
use crate::lob::markout::{
    base_before, future_asof, markouts_for_level, raw_return_bps, MidSample, HORIZONS_MS,
};
use crate::lob::shortlist::{
    build_profile_grid, hour_dependence_test, load_or_write_window, log_hour_test,
    log_profile_trials, repeat_bucket, window_length_days, HourDayObservation, InstrumentCoverage,
    PreregisteredWindow, DISTANCE_BOUNDS_BPS, DISTANCE_LABELS, LIFETIME_LABELS, SIZE_LABELS,
};
use crate::stats::{count_f64, count_f64_u64, BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

use super::{feed_frames, outcome_name, side_name, trade_hit_from_record};
use super::{resolve_h3_mode, ExecutionArgs, H3Args, H3ModeArg};

/// Seed совместного бутстрапа (`net_fill_interval`) — то же число, с которым
/// уже вызывает его боевой пилот (`commands::lob::pilot::run_pilot_battle`):
/// не второе изобретённое, а то же самое, напечатанное в шапке.
const BOOTSTRAP_SEED: u64 = 0;

/// Литерал колонок `unreachable_<horizon>` (см. doc модуля: нет живого
/// отчёта G-LAT — нет числа).
const NOT_MEASURED: &str = "not_measured";

// ---------------------------------------------------------------------------
// Числовые границы осей «размер» и «время жизни» — метки берутся из
// `shortlist`, числа фиксированы здесь той же спекой («Профиль», история 14).
// ---------------------------------------------------------------------------

/// Кратности порога `H3`: `[1,2)`, `[2,4)`, `[4,∞)` (`shortlist::SIZE_LABELS`).
const SIZE_BOUNDS: [(f64, f64); 3] = [(1.0, 2.0), (2.0, 4.0), (4.0, f64::INFINITY)];

/// Время жизни в мс: `[0,1с)`, `[1,10с)`, `[10с,∞)` (`shortlist::LIFETIME_LABELS`).
const LIFETIME_BOUNDS_MS: [(i64, i64); 3] = [(0, 1_000), (1_000, 10_000), (10_000, i64::MAX)];

fn size_bucket(ratio: f64) -> Option<&'static str> {
    SIZE_BOUNDS
        .iter()
        .zip(SIZE_LABELS.iter())
        .find(|((lo, hi), _)| ratio >= *lo && ratio < *hi)
        .map(|(_, label)| *label)
}

fn lifetime_bucket(lifetime_ms: i64) -> Option<&'static str> {
    LIFETIME_BOUNDS_MS
        .iter()
        .zip(LIFETIME_LABELS.iter())
        .find(|((lo, hi), _)| lifetime_ms >= *lo && lifetime_ms < *hi)
        .map(|(_, label)| *label)
}

fn distance_bucket(dist_bps: f64) -> Option<&'static str> {
    DISTANCE_BOUNDS_BPS
        .iter()
        .zip(DISTANCE_LABELS.iter())
        .find(|((lo, hi), _)| dist_bps >= *lo && dist_bps < *hi)
        .map(|(_, label)| *label)
}

/// Расстояние до середины в bps на срезе строго до рождения уровня — тот же
/// приём, что `markout::base_before` использует для смерти (`death_ms`),
/// сдвинутый на рождение (`birth_ms + 1`, чтобы включить срез ровно в момент
/// рождения). `None` — до рождения не было ни одного среза книги.
fn distance_bps_at_birth(mids: &[MidSample], rec: &LevelRecord) -> Option<f64> {
    let (_, mid2x) = base_before(mids, rec.birth_ms.saturating_add(1))?;
    raw_return_bps(mid2x, rec.price_tick.saturating_mul(2)).map(f64::abs)
}

/// Срез середины как есть на `base_ts + horizon_ms` — тот же поиск, что
/// `markout::future_asof`, но возвращает весь срез (нужны обе стороны для
/// спреда выхода `costs::Observation::spread_ticks_exit`), а не только
/// удвоенную середину.
fn mid_sample_asof(mids: &[MidSample], base_ts: i64, horizon_ms: i64) -> Option<MidSample> {
    let target = base_ts.saturating_add(horizon_ms);
    let mut found: Option<MidSample> = None;
    for s in mids {
        if s.ts_ms <= target {
            found = Some(*s);
        } else {
            break;
        }
    }
    found
}

// ---------------------------------------------------------------------------
// Кластер суток для совместного бутстрапа: сутки — целый кластер (A1), тот
// же смысл, что везде в `stats`/`cells`/`costs`.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct DayIndex {
    ids: HashMap<String, i64>,
}

impl DayIndex {
    #[allow(clippy::cast_possible_wrap)]
    fn id(&mut self, day_utc: &str) -> i64 {
        if let Some(&id) = self.ids.get(day_utc) {
            return id;
        }
        let id = self.ids.len() as i64;
        self.ids.insert(day_utc.to_string(), id);
        id
    }
}

// ---------------------------------------------------------------------------
// Каталог сессий: session.json, маркер сверки, реплей одного файла в записи
// уровней и срезы середины. Раскладка и приёмы — те же, что
// `commands::lob::watch` (частный модуль, не переиспользуется напрямую: его
// функции — не `pub`, а зона этого таска не трогает файл watch.rs).
// ---------------------------------------------------------------------------

fn read_verify_marker(path: &Path) -> bool {
    std::fs::read_to_string(path)
        .map(|s| s.trim() == "ok")
        .unwrap_or(false)
}

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

/// Различные сутки среди подкаталогов, по возрастанию — вход
/// `load_or_write_window` для первой записи файла предрегистрации (тот же
/// вход, что `split_calendar` уже берёт у `commands::lob::shortlist`).
/// С таска 23 сутки — по датированным частям каталога
/// (`super::session_days_in_dir`), не по `started_utc` его `session.json`:
/// каталог с частями за D и D+1 даёт обе даты.
fn distinct_session_days(dirs: &[PathBuf]) -> Vec<String> {
    let mut days: BTreeSet<String> = BTreeSet::new();
    for dir in dirs {
        days.extend(super::session_days_in_dir(dir));
    }
    days.into_iter().collect()
}

/// Реплеит части одних суток одной сессии (`super::group_parts_by_day`,
/// таск 23) в записи уровней и срезы середины: кормит книгу и трекер общим
/// `super::feed_frames`, сделки — общим `super::trade_hit_from_record`.
/// Трекер общий на части суток (таск 22, «части читаются подряд как один
/// поток») и чистый на каждые сутки — между сутками разрыв записи есть
/// всегда, и сутки — единица кластера, а не продолжение потока. Книга и
/// `FileReplayer` заводятся заново на каждый файл — тем же приёмом, что
/// `mod.rs::replay_symbol_over_configs` уже применяет к частям суток `lob
/// record` (каждый файл несёт собственный снапшот в начале, продолжать
/// старую книгу через границу файла было бы чтением чужого состояния).
///
/// Возвращает записи **по частям** (в порядке `parts`) — каждая часть несёт
/// свой час старта, и `accumulate_level` получает его от своей части, не от
/// каталога; срезы середины — общие на сутки, как и трекер.
fn replay_one_session_day(
    parts: &[super::SessionPart],
    cfg: LevelsConfig,
) -> anyhow::Result<(Vec<Vec<LevelRecord>>, Vec<MidSample>)> {
    let mut tracker = LevelTracker::new(cfg);
    let mut per_part = Vec::with_capacity(parts.len());
    let mut mids = Vec::new();
    for part in parts {
        let mut records = Vec::new();
        replay_binlog_file_into(&part.path, &mut tracker, &mut records, &mut mids)?;
        per_part.push(records);
    }
    Ok((per_part, mids))
}

/// Один файл-часть через общий трекер; книга и `FileReplayer` — свои.
fn replay_binlog_file_into(
    path: &Path,
    tracker: &mut LevelTracker,
    records: &mut Vec<LevelRecord>,
    mids: &mut Vec<MidSample>,
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
        let frame = reader
            .read_frame()
            .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
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
                feed_frames(&book, tracker, up.cts_ms, records, mids);
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
        feed_frames(&book, tracker, up.cts_ms, records, mids);
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Пул и покрытие.
// ---------------------------------------------------------------------------

#[derive(Debug, serde::Deserialize)]
struct PoolSymbolRow {
    symbol: String,
}

/// Символы пула из `instruments.csv` корня (таск 08) — единственный читатель
/// `pick::instruments_csv_reader`, терпит метку `debug` первой строкой.
fn read_pool_symbols(instruments_csv: &Path) -> anyhow::Result<Vec<String>> {
    let mut r = super::pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — lob profiles читает пул из instruments.csv",
            instruments_csv.display()
        )
    })?;
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

/// Покрытие топ-50 на символ пула из `candidates.csv` (таск 08): единственное
/// место, где это число персистентно (`instruments.csv` корня его не несёт —
/// `interfaces.md`, «Из таска 08»). Терпит метку `debug` первой строкой тем
/// же приёмом, что `pick::instruments_csv_reader`. Символ пула без строки
/// покрытия — громкая ошибка: сетку профилей без него строить нечестно
/// (изобретённое покрытие запрещено §9).
fn read_coverage(
    candidates_csv: &Path,
    pool: &[String],
) -> anyhow::Result<Vec<InstrumentCoverage>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(candidates_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", candidates_csv.display()))?;
    let mut by_symbol: HashMap<String, f64> = HashMap::new();
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
                        "{}: нет покрытия top50 для {s} — lob pick обязан измерить пул до lob profiles",
                        candidates_csv.display()
                    )
                })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Модель исполнения входа за 2 с (R06) — до таска 11 (`lob backtest`,
// `RiskAdverseQueueModel`) такой модели в дереве нет (`interfaces.md`, «Из
// таска 03»: «`filled` не различает…»). Ревью таска 10 (BLOCKING, ось
// Манифест) отклонило более раннюю редакцию с плейсхолдером `filled = true`
// на каждое наблюдение: число получалось правдоподобным (`fill=1.0000`,
// `net_fill == net`), а «`net_fill` — вот это и есть окупается» решает
// судьбу профиля — правдоподобное число без модели исполнения есть выдуманный
// факт (§9). `FillModel` — точка, которой таск 11/12/13 подставит настоящую
// модель через `run_profiles_with_fill_model`; `NoFillModel` — единственная
// реализация сегодня, `lob profiles` (CLI) всегда вызывает `run_profiles`,
// который использует именно её.
// ---------------------------------------------------------------------------

/// Модель исполнения входа за отведённые 2 с (R06). `filled` возвращает
/// `None`, когда модель не имеет мнения об этом наблюдении (сегодня —
/// всегда, `NoFillModel`); `Some(bool)` — когда умеет судить (таск 11+,
/// поверх `lob::backtest`). `label()` идёт в шапку артефакта
/// (`fill_model=…`) — имя источника, а не число, чтобы читатель не принял
/// `not_measured` за рыночный ноль.
///
/// `prime_session` (таск 16) — точка, которой модель, которой нужен настоящий
/// книжный поток сессии (а не только `mids`), гоняет бэктест **один раз на
/// сессию** и кэширует результат: `run_profiles_with_fill_model` вызывает его
/// сразу после реплея каждой сессии, до цикла по уровням, передавая тот же
/// путь к бинлогу и полный список размеченных уровней этой сессии
/// (`interfaces.md`, BLOCKERS таска 13 — «требует реального книжного потока
/// сессии… не перезапуская движок на каждый уровень»). Умолчание — пустая
/// операция: `NoFillModel` и любая модель, которой сессия не нужна, его не
/// переопределяют.
pub trait FillModel {
    /// Таск 22: `_binlog_paths` — все части сессии в порядке записи
    /// (`session_binlog_for`), не один файл — модель, которой нужен книжный
    /// поток (`BacktestFillModel`), обязана прогнать их подряд как один
    /// поток, а не только первую часть.
    fn prime_session(&self, _symbol: &str, _binlog_paths: &[PathBuf], _records: &[LevelRecord]) {}

    fn filled(&self, symbol: &str, rec: &LevelRecord, mids: &[MidSample]) -> Option<bool>;
    fn label(&self) -> &'static str;
}

/// Модель по умолчанию: исполнения не измеряет ни для одного наблюдения —
/// `fill`/`net_fill`/`net_fill_lower` печатают `not_measured` (`write_row`),
/// а не правдоподобное число без основания.
pub struct NoFillModel;

impl FillModel for NoFillModel {
    fn filled(&self, _symbol: &str, _rec: &LevelRecord, _mids: &[MidSample]) -> Option<bool> {
        None
    }

    fn label(&self) -> &'static str {
        "none"
    }
}

// ---------------------------------------------------------------------------
// Накопитель профиля.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct HorizonAgg {
    /// `net_bps` здесь — сырое подписанное `m` этого горизонта: тот же приём
    /// совместного бутстрапа с `filled = true`, что и `pilot.rs::m10s`
    /// (doc модуля) — внутренняя механика интервала, не про исполнение входа.
    m_obs: Vec<FillObservation>,
    /// Сырое движение середины **без нормировки знаком по стороне**
    /// (`markout::raw_return_bps`, без применения `σ`) — знак сохраняется:
    /// зеркальная пара бид/аск даёт одинаковый `m`, но противоположный
    /// `raw` (`PLAN.md` §11 п.5; тест
    /// `mirrored_moves_give_equal_m_and_opposite_signed_raw`).
    raw_vals: Vec<f64>,
}

#[derive(Default)]
struct ProfileAgg {
    eaten: u64,
    pulled: u64,
    mixed: u64,
    horizons: [HorizonAgg; 4],
    /// `net` — средний `m` за вычетом издержек на горизонте 10 с
    /// (`costs::mean_net_bps`), без веса на исполнение.
    net_obs: Vec<Observation>,
    /// `fill`/`net_fill` — на горизонте 10 с; наполняется только когда
    /// `FillModel::filled` возвращает `Some` (см. doc выше про
    /// `NoFillModel`/`fill_model`).
    fill_obs: Vec<FillObservation>,
    hours: BTreeSet<u32>,
    /// Годные сутки, на которые пришлось хотя бы одно наблюдение этого
    /// профиля — `G` для `shortlist::decide_profile`/подтверждающей шапки
    /// (ремонт по ревью таска 12, открытый пункт (1) для таска 13:
    /// `docs/findings/profiles-*.csv` раньше не несло числа суток на
    /// профиль, и `G` на подтверждающей был захардкожен нулём).
    days: BTreeSet<i64>,
    /// Сутки → (сумма часов старта, число наблюдений часа, сумма `m_10s`) —
    /// вход теста на зависимость от часа суток (`shortlist::
    /// hour_dependence_test`, ticket 06/13): один кластер — одни сутки,
    /// значение — среднее `m_10s` за эти сутки (тот же горизонт, что идёт в
    /// гейт G2 через `net_obs`), час — средний час старта сессий этих суток
    /// (doc `HourDayObservation`: «при нескольких сессиях за сутки — среднее
    /// их часов»).
    hour_day_sums: BTreeMap<i64, (f64, u32, f64)>,
}

#[allow(clippy::too_many_arguments)]
fn apply_observation(
    agg: &mut ProfileAgg,
    outcome: Outcome,
    m_by_horizon: [Option<f64>; 4],
    base: Option<(i64, i64)>,
    mids: &[MidSample],
    day_id: i64,
    start_hour_utc: u32,
    symbol: &str,
    rec: &LevelRecord,
    fill_model: &dyn FillModel,
) {
    match outcome {
        Outcome::Eaten => agg.eaten += 1,
        Outcome::Pulled => agg.pulled += 1,
        Outcome::Mixed => agg.mixed += 1,
    }
    agg.hours.insert(start_hour_utc);
    agg.days.insert(day_id);
    for (i, h) in HORIZONS_MS.iter().enumerate() {
        let Some(m) = m_by_horizon[i] else { continue };
        agg.horizons[i].m_obs.push(FillObservation {
            day_cluster: day_id,
            net_bps: m,
            filled: true,
        });
        if let Some((base_ts, base2x)) = base {
            if let Some(fut2x) = future_asof(mids, base_ts, *h) {
                if let Some(raw) = raw_return_bps(base2x, fut2x) {
                    // Без `.abs()`: «сырое» значит без нормировки знаком по
                    // стороне (σ), а не без знака вовсе — см. doc `raw_vals`.
                    agg.horizons[i].raw_vals.push(raw);
                }
            }
        }
    }
    if let (Some(m10), Some((base_ts, base2x))) = (m_by_horizon[2], base) {
        // Тест на час суток (`shortlist::hour_dependence_test`) — на том же
        // горизонте 10 с, что и `net_obs` ниже (вердиктная ячейка G2).
        let hour_entry = agg.hour_day_sums.entry(day_id).or_insert((0.0, 0, 0.0));
        hour_entry.0 += f64::from(start_hour_utc);
        hour_entry.1 += 1;
        hour_entry.2 += m10;
        if let Some(exit) = mid_sample_asof(mids, base_ts, HORIZONS_MS[2]) {
            let spread = exit.ask_tick - exit.bid_tick;
            agg.net_obs.push(Observation {
                m_bps: m10,
                spread_ticks_exit: spread,
                mid2x_base: base2x,
            });
            if let Some(net) = cost_net_bps(m10, spread, base2x) {
                if let Some(filled) = fill_model.filled(symbol, rec, mids) {
                    agg.fill_obs.push(FillObservation {
                        day_cluster: day_id,
                        net_bps: net,
                        filled,
                    });
                }
            }
        }
    }
}

/// Классифицирует один уровень по семи осям и разносит его наблюдение по
/// всем совпавшим id сетки: маргиналы совпадают всегда (кроме осей без
/// определённого бакета — размер без положительного `h3_lots_value`,
/// расстояние без среза до рождения), крест — только если пара
/// инструмент×расстояние пригодна (Decision 26а: такого id просто нет в
/// `profiles`, `get_mut` вернёт `None`, не панику).
#[allow(clippy::too_many_arguments)]
fn accumulate_level(
    profiles: &mut BTreeMap<String, ProfileAgg>,
    symbol: &str,
    rec: &LevelRecord,
    mids: &[MidSample],
    day_id: i64,
    start_hour_utc: u32,
    h3_lots_value: i64,
    fill_model: &dyn FillModel,
) {
    let side_label = side_name(rec.side);
    let outcome = rec.outcome();
    let outcome_label = outcome_name(outcome);
    let repeat_label = repeat_bucket(rec.repeat_count);
    let size_label = if h3_lots_value > 0 {
        #[allow(clippy::cast_precision_loss)]
        let ratio = rec.size_max as f64 / h3_lots_value as f64;
        size_bucket(ratio)
    } else {
        None
    };
    let life_label = lifetime_bucket(rec.lifetime_ms);
    let dist_bps = distance_bps_at_birth(mids, rec);
    let dist_label = dist_bps.and_then(distance_bucket);

    let mut ids = vec![
        format!("marginal:instrument={symbol}"),
        format!("marginal:side={side_label}"),
        format!("marginal:outcome={outcome_label}"),
        format!("marginal:repeat={repeat_label}"),
    ];
    if let Some(sl) = size_label {
        ids.push(format!("marginal:size={sl}"));
    }
    if let Some(ll) = life_label {
        ids.push(format!("marginal:life={ll}"));
    }
    if let Some(dl) = dist_label {
        ids.push(format!("marginal:dist={dl}"));
    }

    let m_by_horizon = markouts_for_level(rec, mids);
    let base = base_before(mids, rec.death_ms);

    for id in &ids {
        let agg = profiles
            .get_mut(id)
            .expect("маргинальный id всегда есть в сетке build_profile_grid");
        apply_observation(
            agg,
            outcome,
            m_by_horizon,
            base,
            mids,
            day_id,
            start_hour_utc,
            symbol,
            rec,
            fill_model,
        );
    }
    if let Some(dl) = dist_label {
        let cross_id = format!("cross:{symbol}|{outcome_label}|{dl}");
        // Пары инструмент×расстояние вне покрытия (Decision 26а) в сетке
        // отсутствуют вовсе — `get_mut` даёт `None`, наблюдение молча не
        // засчитывается непригодной корзине (не ошибка).
        if let Some(agg) = profiles.get_mut(&cross_id) {
            apply_observation(
                agg,
                outcome,
                m_by_horizon,
                base,
                mids,
                day_id,
                start_hour_utc,
                symbol,
                rec,
                fill_model,
            );
        }
    }
}

// ---------------------------------------------------------------------------
// CLI.
// ---------------------------------------------------------------------------

/// Аргументы `lob profiles`. `--h3-mode` обязателен (план D-H3, тот же
/// выбор, что у `levels`/`markout`/`watch`/`pilot`).
#[derive(Debug, Args)]
pub struct ProfilesArgs {
    /// Корень: `instruments.csv` пула плюс подкаталог на каждую сессию.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Полная таблица кандидатов (`lob pick`) — источник `coverage_top50_bps`.
    #[arg(long, default_value = "docs/plan/candidates.csv")]
    pub candidates_csv: PathBuf,
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд).
    #[command(flatten)]
    pub h3: H3Args,
    /// Прогрев в мс, на сессию.
    #[arg(long, default_value_t = super::DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс — печатается в шапке файла (R57).
    #[arg(long, default_value_t = super::DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Отладочный вход без маркеров сверки (`data/session-debug/…`, doc
    /// модуля): снимает требование `verify-<SYMBOL>.status == ok`, метка
    /// `debug` в шапке, `runs.csv` не пишется.
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
    /// Куда писать (по умолчанию `docs/findings/profiles-<дата>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Момент для имени файла и шапки UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
    /// Журнал испытаний (шаг 7.1) — только боевой режим.
    #[arg(long, default_value = "docs/plan/runs.csv")]
    pub runs_out: PathBuf,
    /// Тройка `BacktestFillModel`: `--median-rtt-ns`, `--p95-rtt-ns`,
    /// `--order-qty-e9` (общие для `profiles`/`shortlist`).
    #[command(flatten)]
    pub execution: ExecutionArgs,
    /// Файл-предрегистрация окна «сейчас» (ticket 21, R57) — та же граница
    /// разведочная/подтверждающая, тот же файл, что `lob shortlist
    /// --preregistration`: первый прогон пишет, следующие — читают
    /// (`crate::lob::shortlist::load_or_write_window`). Обязателен без
    /// `--allow-unverified` — без него окно не определено, ошибка, не тихое
    /// «все сессии».
    #[arg(long)]
    pub preregistration: Option<PathBuf>,
    /// Конец окна «сейчас», только когда файл предрегистрации несёт одну
    /// границу без конца (`confirmatory:` пуста) — дописывается в файл один
    /// раз, тем же приёмом, что сама граница.
    #[arg(long)]
    pub window_end: Option<String>,
}

/// Итог `lob profiles` для печати диспетчером.
#[derive(Debug)]
pub struct ProfilesSummary {
    pub rows: usize,
    pub out: PathBuf,
    pub debug: bool,
}

/// Строка `window: …` шапки (ticket 21, R57) — общий формат для
/// `profiles-<дата>.csv` и `shortlist-<дата>.md`
/// (`commands::lob::shortlist::run_shortlist` печатает её той же функцией):
/// `debug (all sessions)` в отладке, иначе границы плюс длина в сутках
/// (из границ, не из числа сессий), число сессий внутри/вне окна и сама
/// пара разведочная/подтверждающая, из которой окно выведено.
pub(crate) fn format_window_line(
    window: &Option<PreregisteredWindow>,
    sessions_in_window: usize,
    sessions_outside_window: usize,
) -> anyhow::Result<String> {
    let Some(w) = window else {
        return Ok("window: debug (all sessions)".to_string());
    };
    let days = window_length_days(w).map_err(|e| anyhow::anyhow!("{e}"))?;
    let expl_start = w.exploratory.first().cloned().unwrap_or_default();
    let expl_end = w.exploratory.last().cloned().unwrap_or_default();
    let conf_start = w.confirmatory.first().cloned().unwrap_or_default();
    let conf_end = w.confirmatory.last().cloned().unwrap_or_default();
    Ok(format!(
        "window: {}..{} days={days} sessions={sessions_in_window} \
         sessions_outside_window={sessions_outside_window} exploratory={expl_start}..{expl_end} \
         confirmatory={conf_start}..{conf_end}",
        w.start, w.end
    ))
}

fn h3_mode_label(mode: H3ModeArg) -> &'static str {
    match mode {
        H3ModeArg::Floor => "floor",
        H3ModeArg::Percentile => "percentile",
    }
}

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.6}"),
        None => "none".to_string(),
    }
}

fn mean(vals: &[f64]) -> Option<f64> {
    if vals.is_empty() {
        return None;
    }
    Some(vals.iter().sum::<f64>() / count_f64(vals.len()))
}

struct HorizonColumns {
    m: Option<f64>,
    m_lower: Option<f64>,
    raw: Option<f64>,
}

fn horizon_columns(agg: &HorizonAgg) -> HorizonColumns {
    let interval = net_fill_interval(
        &agg.m_obs,
        GATE_ALPHA,
        BOOTSTRAP_REPLICATIONS,
        BOOTSTRAP_SEED,
    );
    HorizonColumns {
        m: interval.map(|i| i.point_bps),
        m_lower: interval.map(|i| i.lower_bps),
        raw: mean(&agg.raw_vals),
    }
}

/// Шапка `profiles-<дата>.csv`: 5 общих колонок плюс 4 на каждый из четырёх
/// горизонтов (`m`, нижняя граница, сырое движение, флаг `unreachable`)
/// плюс деньги плюс час старта сессий плюс `g` (ремонт по ревью таска 12,
/// открытый пункт (1) для таска 13). `g` — новая, дописанная в конец колонка:
/// `commands::lob::shortlist::read_profile_table` матчит по имени, а не по
/// позиции (доктрока там же), так что дописывание в конец не ломает читателя.
const HEADER: [&str; 28] = [
    "profile_id",
    "n",
    "eaten_share",
    "pulled_share",
    "mixed_share",
    "m_100ms",
    "m_100ms_lower",
    "raw_100ms",
    "unreachable_100ms",
    "m_1000ms",
    "m_1000ms_lower",
    "raw_1000ms",
    "unreachable_1000ms",
    "m_10000ms",
    "m_10000ms_lower",
    "raw_10000ms",
    "unreachable_10000ms",
    "m_60000ms",
    "m_60000ms_lower",
    "raw_60000ms",
    "unreachable_60000ms",
    "net_bps",
    "fill",
    "net_fill",
    "net_fill_lower",
    "session_start_hours_utc",
    "g",
    "observed_sharpe",
];

/// `measured` — есть ли активная модель исполнения (`FillModel::label() !=
/// "none"`): `false` печатает `not_measured` в `fill`/`net_fill`/
/// `net_fill_lower` буквально, независимо от того, пуст ли `agg.fill_obs`
/// (он и обязан быть пуст под `NoFillModel` — см. doc `ProfileAgg`), а не
/// молчаливый `none`, который читатель мог бы принять за измеренный ноль.
fn write_row(
    w: &mut csv::Writer<std::fs::File>,
    id: &str,
    agg: &ProfileAgg,
    measured: bool,
) -> anyhow::Result<()> {
    let n = agg.eaten + agg.pulled + agg.mixed;
    let (eaten_share, pulled_share, mixed_share) = if n > 0 {
        let nf = count_f64_u64(n);
        (
            Some(count_f64_u64(agg.eaten) / nf),
            Some(count_f64_u64(agg.pulled) / nf),
            Some(count_f64_u64(agg.mixed) / nf),
        )
    } else {
        (None, None, None)
    };
    let hc: Vec<HorizonColumns> = agg.horizons.iter().map(horizon_columns).collect();
    let net = mean_net_bps(&agg.net_obs);
    let (fill_col, net_fill_col, net_fill_lower_col) = if measured {
        let fill = fill_rate(&agg.fill_obs);
        let net_fill_point = net_fill_bps(&agg.fill_obs);
        let net_fill_lower = net_fill_interval(
            &agg.fill_obs,
            GATE_ALPHA,
            BOOTSTRAP_REPLICATIONS,
            BOOTSTRAP_SEED,
        )
        .map(|i| i.lower_bps);
        (
            format_fill_column(fill),
            fmt_opt(net_fill_point),
            fmt_opt(net_fill_lower),
        )
    } else {
        (
            NOT_MEASURED.to_string(),
            NOT_MEASURED.to_string(),
            NOT_MEASURED.to_string(),
        )
    };
    let hours: Vec<String> = agg.hours.iter().map(ToString::to_string).collect();
    // `observed_sharpe` (таск 16) — Шарп круговых net **исполнившихся**
    // входов (`fill_obs` фильтрован по `filled == true`), в единицах на
    // наблюдение — то же самое множество наблюдений, из которого уже
    // считаются `fill`/`net_fill`/`net_fill_lower` (doc `FillModel`, «из тех
    // же наблюдений», ticket 16). `not_measured` без активной модели
    // (`measured == false`, `fill_obs` тогда и так пуст); `none` — модель
    // активна, но исполнений меньше двух (`sharpe_ratio` требует дисперсию).
    let observed_sharpe_col = if measured {
        let filled_net: Vec<f64> = agg
            .fill_obs
            .iter()
            .filter(|o| o.filled)
            .map(|o| o.net_bps)
            .collect();
        fmt_opt(sharpe_ratio(&filled_net))
    } else {
        NOT_MEASURED.to_string()
    };

    let mut record = vec![
        id.to_string(),
        n.to_string(),
        fmt_opt(eaten_share),
        fmt_opt(pulled_share),
        fmt_opt(mixed_share),
    ];
    for h in &hc {
        record.push(fmt_opt(h.m));
        record.push(fmt_opt(h.m_lower));
        record.push(fmt_opt(h.raw));
        record.push(NOT_MEASURED.to_string());
    }
    record.push(fmt_opt(net));
    record.push(fill_col);
    record.push(net_fill_col);
    record.push(net_fill_lower_col);
    record.push(hours.join(","));
    record.push(agg.days.len().to_string());
    record.push(observed_sharpe_col);
    w.write_record(record)?;
    Ok(())
}

fn default_out_path(now: &str) -> PathBuf {
    let date = now.get(..10).unwrap_or("1970-01-01");
    PathBuf::from(format!("docs/findings/profiles-{date}.csv"))
}

/// Точка входа `lob profiles` (таск 10, CLI): та же логика, что
/// `run_profiles_with_fill_model`, всегда с `NoFillModel` — сегодня в
/// дереве нет модели исполнения (см. doc `FillModel`), и подставлять
/// правдоподобную самому запрещено (§9). Когда таск 11/12/13 подключит
/// бэктест, вызывающий (например, будущая подкоманда или `pilot`) заменит
/// этот вызов на `run_profiles_with_fill_model(args, &RealModel::new(..))` —
/// сама сборка сетки и запись CSV не изменится ни строкой.
pub fn run_profiles(args: &ProfilesArgs) -> anyhow::Result<ProfilesSummary> {
    let model = resolve_fill_model(
        args.execution.median_rtt_ns,
        args.execution.p95_rtt_ns,
        args.execution.order_qty_e9,
    )?;
    run_profiles_with_fill_model(args, model.as_ref())
}

/// Строит модель исполнения из тройки RTT/лота площадки (таск 16): заданы все
/// три — `BacktestFillModel` поверх `lob::backtest` (`fill_model=backtest`
/// шапки); не задан ни один — `NoFillModel`, как до этого таска. Задать
/// только часть тройки — отказ: у RTT/лота нет правдоподобного умолчания
/// (§9), а тройка нужна вся, чтобы вообще собрать бэктест-модель. Общая точка
/// для `lob profiles` (`run_profiles`), `lob shortlist`
/// (`commands::lob::shortlist::run_profiles_over`) и `pilot --debug`
/// (`pilot.rs` передаёт `None`-тройку намеренно — лот площадки там per-symbol,
/// не один на пул).
pub fn resolve_fill_model(
    median_rtt_ns: Option<i64>,
    p95_rtt_ns: Option<i64>,
    order_qty_e9: Option<i64>,
) -> anyhow::Result<Box<dyn FillModel>> {
    match (median_rtt_ns, p95_rtt_ns, order_qty_e9) {
        (Some(median), Some(p95), Some(qty)) => Ok(Box::new(
            super::backtest::BacktestFillModel::new(median, p95, qty),
        )),
        (None, None, None) => Ok(Box::new(NoFillModel)),
        _ => anyhow::bail!(
            "--median-rtt-ns/--p95-rtt-ns/--order-qty-e9 обязаны быть заданы все втроём или ни \
             один — модель исполнения либо задана целиком (числа измерены), либо не задана \
             вовсе (§9: изобретённое число запрещено)"
        ),
    }
}

/// То же самое, но с явной моделью исполнения — семь осей, сетка
/// `shortlist::build_profile_grid`, реплей каждой сессии каждого символа
/// пула, полная таблица. `fill_model` решает, какие наблюдения попадают в
/// `fill_obs` (см. doc `FillModel`/`ProfileAgg`); шапка артефакта несёт его
/// `label()` (`fill_model=none|backtest|…`), и `fill`/`net_fill`/
/// `net_fill_lower` печатают `not_measured`, когда `label() == "none"`.
pub fn run_profiles_with_fill_model(
    args: &ProfilesArgs,
    fill_model: &dyn FillModel,
) -> anyhow::Result<ProfilesSummary> {
    let instruments_csv = crate::commands::record::instruments_csv_path(&args.root);
    let pool = read_pool_symbols(&instruments_csv)?;
    let coverages = read_coverage(&args.candidates_csv, &pool)?;
    let grid = build_profile_grid(&coverages)?;

    let mut profiles: BTreeMap<String, ProfileAgg> = grid
        .iter()
        .cloned()
        .map(|id| (id, ProfileAgg::default()))
        .collect();

    let dirs = session_dirs(&args.root)?;
    let mut day_index = DayIndex::default();

    // Окно «сейчас» (ticket 21, R57): слепая приёмка поймала эту функцию на
    // чтении всех подкаталогов `root` без окна — длина нигде не назначалась
    // до данных. Отладочный вход (`--allow-unverified`) не сужает окно (doc
    // модуля: «данными не является»); боевой вход обязан назвать файл
    // предрегистрации, иначе это тихое «все сессии», которое и поймало
    // ревью — громкая ошибка вместо него.
    let window = if args.allow_unverified {
        None
    } else {
        let Some(preregistration) = &args.preregistration else {
            anyhow::bail!(
                "--preregistration обязателен без --allow-unverified — окно «сейчас» не \
                 определено (R57, не тихое «все сессии»)"
            );
        };
        let days_now = distinct_session_days(&dirs);
        Some(
            load_or_write_window(preregistration, &days_now, args.window_end.as_deref())
                .map_err(|e| anyhow::anyhow!("{e}"))?,
        )
    };
    // Счёт внутри/снаружи окна — по парам (каталог, сутки), таск 23: каталог
    // с частями за двое суток — две сессионные единицы, по одной на день.
    let mut sessions_outside_window = 0usize;
    let mut sessions_in_window = 0usize;
    for dir in &dirs {
        for day in super::session_days_in_dir(dir) {
            match &window {
                Some(w) if !w.contains_day(&day) => sessions_outside_window += 1,
                _ => sessions_in_window += 1,
            }
        }
    }

    for symbol in &pool {
        let mode = resolve_h3_mode(&args.root, symbol, args.h3.h3_mode, args.h3.h3_lots)?;
        let h3_lots_value = match mode {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => h3_lots,
        };
        let cfg = LevelsConfig {
            mode,
            warmup_ms: args.warmup_ms,
            repeat_window_ms: args.repeat_window_ms,
        };
        for dir in &dirs {
            // Таск 19/23: резолвер сессии (`<SYMBOL>-<день>[-pN].binlog` с
            // сутками и часом старта на каждую часть) — нет `session.json`
            // или файла для этого символа в этой сессии означает «не сессия
            // этого символа», не ошибку (doc модуля, тот же приём, что
            // `watch.rs`); резолвер также не молчит про старый формат, но
            // здесь, на обходе многих каталогов подряд, это тоже мягкий
            // пропуск — не рвать всю таблицу профилей из-за одного каталога.
            let Ok(parts) = super::session_parts_for(dir, symbol) else {
                continue;
            };
            let verified = read_verify_marker(&dir.join(format!("verify-{symbol}.status")));
            if !verified && !args.allow_unverified {
                continue;
            }
            for (day_utc, day_parts) in super::group_parts_by_day(parts) {
                // Окно «сейчас»: сутки вне окна не читаются вовсе (не только
                // не считаются) — проверка до реплея, симметрично
                // `sessions_outside_window` выше; с таска 23 — по суткам
                // части, не каталога.
                if let Some(w) = &window {
                    if !w.contains_day(&day_utc) {
                        continue;
                    }
                }
                let (records_per_part, mids) = replay_one_session_day(&day_parts, cfg)?;
                let day_paths: Vec<PathBuf> = day_parts.iter().map(|p| p.path.clone()).collect();
                let day_records: Vec<LevelRecord> =
                    records_per_part.iter().flatten().cloned().collect();
                // Один прогон движка на сутки сессии (таск 16, doc
                // `FillModel::prime_session`): модель, которой нужен книжный
                // поток, гонит `lob::backtest` здесь и кэширует ответы;
                // `filled` ниже только читает кэш. Таск 22: сутки могут нести
                // несколько частей — модель получает все пути и сама склеивает
                // событийный поток (`BacktestFillModel::prime_session`).
                fill_model.prime_session(symbol, &day_paths, &day_records);
                let day_id = day_index.id(&day_utc);
                for (part, records) in day_parts.iter().zip(&records_per_part) {
                    for rec in records {
                        accumulate_level(
                            &mut profiles,
                            symbol,
                            rec,
                            &mids,
                            day_id,
                            part.start_hour_utc,
                            h3_lots_value,
                            fill_model,
                        );
                    }
                }
            }
        }
    }

    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let out = args.out.clone().unwrap_or_else(|| default_out_path(&now));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut file = std::fs::File::create(&out)?;
    let debug_suffix = if args.allow_unverified { " debug" } else { "" };
    let fill_model_label = fill_model.label();
    let measured = fill_model_label != "none";
    writeln!(
        file,
        "# lob profiles: h3_mode={} warmup_ms={} repeat_window_ms={} alpha={GATE_ALPHA} replications={BOOTSTRAP_REPLICATIONS} seed={BOOTSTRAP_SEED} fill_model={fill_model_label}{debug_suffix}",
        h3_mode_label(args.h3.h3_mode),
        args.warmup_ms,
        args.repeat_window_ms,
    )?;
    writeln!(
        file,
        "# {}",
        format_window_line(&window, sessions_in_window, sessions_outside_window)?
    )?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(HEADER)?;
    for (id, agg) in &profiles {
        write_row(&mut w, id, agg, measured)?;
    }
    w.flush()?;

    if !args.allow_unverified {
        log_profile_trials(&args.runs_out, &now, &grid)?;
        // Тест на зависимость от часа суток (ticket 06/13, `interfaces.md`
        // «Из таска 12», открытый пункт (2)): каждый посчитанный тест на час
        // — своя строка `runs.csv`, тем же видом испытания, что профиль
        // (`total_trials` считает строки, не сетку отдельно). Отказ
        // (`HourTestError`) не пишется: методический отказ («суток мало»,
        // «сетка Уэбба грубее альфы», «наблюдаемая вырождена») не есть
        // испытание, как непригодная корзина сетки — её тоже нет в выходе.
        for (id, agg) in &profiles {
            let obs: Vec<HourDayObservation> = agg
                .hour_day_sums
                .iter()
                .map(
                    |(&day, &(hour_sum, hour_n, value_sum))| HourDayObservation {
                        day,
                        hour_utc: hour_sum / f64::from(hour_n),
                        value: value_sum / f64::from(hour_n),
                    },
                )
                .collect();
            if let Ok(p) = hour_dependence_test(&obs, BOOTSTRAP_REPLICATIONS, BOOTSTRAP_SEED) {
                log_hour_test(&args.runs_out, &now, id, p)?;
            }
        }
    }

    Ok(ProfilesSummary {
        rows: profiles.len(),
        out,
        debug: args.allow_unverified,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Метки числовых границ, введённых этим файлом (`SIZE_BOUNDS`,
    /// `LIFETIME_BOUNDS_MS`), обязаны совпасть по количеству и порядку с
    /// `shortlist::SIZE_LABELS`/`LIFETIME_LABELS`: у этих двух осей нет
    /// собственной парной числовой таблицы в `shortlist.rs` (в отличие от
    /// `DISTANCE_BOUNDS_BPS`), и дрейф между числом здесь и меткой там иначе
    /// не поймать компилятором.
    #[test]
    fn size_and_lifetime_bounds_match_shortlist_labels() {
        assert_eq!(SIZE_BOUNDS.len(), SIZE_LABELS.len());
        assert_eq!(LIFETIME_BOUNDS_MS.len(), LIFETIME_LABELS.len());
        assert_eq!(size_bucket(1.0), Some(SIZE_LABELS[0]));
        assert_eq!(size_bucket(1.999), Some(SIZE_LABELS[0]));
        assert_eq!(size_bucket(2.0), Some(SIZE_LABELS[1]));
        assert_eq!(size_bucket(4.0), Some(SIZE_LABELS[2]));
        assert_eq!(size_bucket(0.5), None, "ниже порога H3 — вне сетки размера");
        assert_eq!(lifetime_bucket(0), Some(LIFETIME_LABELS[0]));
        assert_eq!(lifetime_bucket(999), Some(LIFETIME_LABELS[0]));
        assert_eq!(lifetime_bucket(1_000), Some(LIFETIME_LABELS[1]));
        assert_eq!(lifetime_bucket(10_000), Some(LIFETIME_LABELS[2]));
    }

    #[test]
    fn distance_bucket_matches_shortlist_bounds() {
        assert_eq!(distance_bucket(0.0), Some(DISTANCE_LABELS[0]));
        assert_eq!(distance_bucket(0.999), Some(DISTANCE_LABELS[0]));
        assert_eq!(distance_bucket(1.0), Some(DISTANCE_LABELS[1]));
        assert_eq!(distance_bucket(24.999), Some(DISTANCE_LABELS[4]));
        assert_eq!(distance_bucket(25.0), None, "за пределами сетки расстояния");
    }

    fn write_instruments_csv(root: &Path, symbols: &[(&str, i64)]) {
        let mut text =
            String::from("symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n");
        for (symbol, h3_lots) in symbols {
            text.push_str(&format!("{symbol},0.01,0.1,0.1,5,{h3_lots}\n"));
        }
        std::fs::write(crate::commands::record::instruments_csv_path(root), text).unwrap();
    }

    fn write_candidates_csv(path: &Path, rows: &[(&str, f64)]) {
        let mut text = String::from("symbol,coverage_top50_bps\n");
        for (symbol, coverage) in rows {
            text.push_str(&format!("{symbol},{coverage}\n"));
        }
        std::fs::write(path, text).unwrap();
    }

    fn write_session_dir(
        root: &Path,
        session_id: &str,
        symbol: &str,
        started_utc: &str,
        start_hour_utc: u32,
        verified: bool,
        frames: &[Vec<crate::binlog::Record>],
    ) {
        let dir = root.join(session_id);
        std::fs::create_dir_all(&dir).unwrap();
        let json = format!(
            "{{\"started_utc\":\"{started_utc}\",\"start_hour_utc\":{start_hour_utc},\
             \"instruments\":[\"{symbol}\"]}}"
        );
        std::fs::write(dir.join("session.json"), json).unwrap();
        let header = crate::binlog::Header {
            tick_e9: super::super::test_support::FIX_TICK_E9,
            step_e9: 1_000_000,
            max_records_per_frame: 4096,
        };
        let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
        for f in frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        // Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
        // `<SYMBOL>.binlog` — фикстура следует той же раскладке, которую
        // теперь ждёт `session_binlog_for`.
        let day = &started_utc[..10];
        std::fs::write(dir.join(format!("{symbol}-{day}.binlog")), w.into_inner()).unwrap();
        if verified {
            std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok").unwrap();
        }
    }

    /// Окно «сейчас» (ticket 21) для тестов, которым сам механизм окна
    /// безразличен: широкий диапазон, заведомо накрывающий любую дату
    /// фикстур этого файла (2000..2099), написан один раз — так тесты не
    /// обязаны держать по двое суток ради `split_calendar`. Не переписывает
    /// файл, если он уже существует: тесты самого окна пишут свой, более
    /// узкий, файл до вызова `base_args`.
    fn write_wide_preregistration(root: &Path) -> PathBuf {
        let path = root.join("preregistration.md");
        if !path.is_file() {
            std::fs::write(&path, "exploratory: 2000-01-01\nconfirmatory: 2099-12-31\n").unwrap();
        }
        path
    }

    fn base_args(root: &Path, candidates_csv: PathBuf, out: PathBuf) -> ProfilesArgs {
        ProfilesArgs {
            root: root.to_path_buf(),
            candidates_csv,
            h3: H3Args {
                h3_mode: H3ModeArg::Floor,
                h3_lots: None,
            },
            warmup_ms: 0,
            repeat_window_ms: super::super::DEFAULT_REPEAT_WINDOW_MS,
            allow_unverified: false,
            out: Some(out),
            now_utc: Some("2026-09-08T00:00:00Z".to_string()),
            runs_out: root.join("runs.csv"),
            execution: ExecutionArgs {
                median_rtt_ns: None,
                p95_rtt_ns: None,
                order_qty_e9: None,
            },
            preregistration: Some(write_wide_preregistration(root)),
            window_end: None,
        }
    }

    /// Таск 17, пункт 5г: `hour_dependence_test` отказывает («сутки < G_MIN»)
    /// на фикстурах остального файла — они держат один-два дня. Здесь семь
    /// **разных** суток одного профиля (`marginal:instrument=SOLUSDT`) с
    /// разным часом старта (`start_hour_utc = d`) и разным движением цены
    /// (ask сдвигается на `2*d` тиков внутри горизонта 10 с той же сессии) —
    /// день и наблюдение линейно связаны тем же приёмом, что
    /// `shortlist::hour_dependence_test_rejects_strong_known_trend`, только
    /// через настоящий бинлог, а не готовый `HourDayObservation`. Уровень:
    /// рождается снапшотом (бид 100@10, аск 200@10), в 1с бид падает до 1
    /// лота — `5*1 < 10` (`below_fraction`) роняет его немедленно
    /// (`DeathKind::BelowFraction`), база markout — срез в момент рождения
    /// (t=0, mid=300); в 9с (≤ горизонта 10с от базы) аск подтягивается на
    /// `2*d` тиков — `future_asof` подхватывает его до цели `10_000`мс,
    /// `m_10s` растёт вместе с `d`. Критерий — не число `p`, а сам факт: цепочка
    /// дошла до `Ok`, и `log_hour_test` дописал строку `runs.csv`.
    #[test]
    fn hour_dependence_test_reaches_ok_with_seven_days_and_logs_a_runs_csv_row() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);

        for d in 1..=7i64 {
            let shift = 2 * d;
            let frames = vec![
                super::super::test_support::snap_frame(0, &[(100, 10)], &[(200, 10)]),
                super::super::test_support::delta_frame(1_000, &[(100, 1)], &[]),
                super::super::test_support::delta_frame(9_000, &[], &[(200 - shift, 10)]),
            ];
            write_session_dir(
                root,
                &format!("day-{d}"),
                "SOLUSDT",
                &format!("2026-09-0{d}T00:00:00Z"),
                d as u32,
                true,
                &frames,
            );
        }

        let out = root.join("profiles.csv");
        let args = base_args(root, candidates_csv, out.clone());
        run_profiles(&args).expect("боевой прогон на семи сутках");

        let rows = crate::lob::runs::read_run_rows(&args.runs_out).unwrap();
        let hour_rows: Vec<_> = rows
            .iter()
            .filter(|r| {
                r.detail
                    .starts_with("hour_test marginal:instrument=SOLUSDT ")
            })
            .collect();
        assert!(
            !hour_rows.is_empty(),
            "семь суток с разными часами и разным движением обязаны довести \
             hour_dependence_test до Ok и дать строку hour_test в runs.csv: {rows:?}"
        );
    }

    /// Критерий приёмки таска 10, буквально: снести файл, прогнать команду
    /// на тех же входах, получить те же числа. Заодно проверяет: таблица
    /// полная (профиль без наблюдений остаётся строкой с `n=0`, а не
    /// исчезает), непригодная корзина расстояния отсутствует (ZEC с
    /// покрытием 4 bps не даёт `cross:ZECUSDT|...|[10,25)`), шапка несёт
    /// длину окна и seed, `unreachable_*` печатает `not_measured`.
    #[test]
    fn profiles_csv_is_complete_and_deterministic() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5), ("ZECUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0), ("ZECUSDT", 4.0)]);

        let frames = super::super::test_support::three_level_frames();
        write_session_dir(
            root,
            "2026-09-08T020000Z",
            "SOLUSDT",
            "2026-09-08T02:00:00Z",
            2,
            true,
            &frames,
        );
        write_session_dir(
            root,
            "2026-09-08T020000Z",
            "ZECUSDT",
            "2026-09-08T02:00:00Z",
            2,
            true,
            &frames,
        );
        // Не верифицированная сессия того же символа — обязана быть
        // пропущена в боевом режиме (verified=false, --allow-unverified не
        // передан), иначе детерминизм и полнота были бы случайностью.
        write_session_dir(
            root,
            "2026-09-09T050000Z",
            "SOLUSDT",
            "2026-09-09T05:00:00Z",
            5,
            false,
            &frames,
        );

        let out = root.join("profiles.csv");
        let args = base_args(root, candidates_csv, out.clone());

        let first = run_profiles(&args).expect("первый прогон");
        let bytes_first = std::fs::read(&out).unwrap();
        std::fs::remove_file(&out).unwrap();
        let second = run_profiles(&args).expect("второй прогон после сноса файла");
        let bytes_second = std::fs::read(&out).unwrap();

        assert_eq!(
            bytes_first, bytes_second,
            "снести файл и прогнать снова — те же числа"
        );
        assert_eq!(first.rows, second.rows);
        assert!(!first.debug);

        let text = String::from_utf8(bytes_second).unwrap();
        let mut lines = text.lines();
        let header_comment = lines.next().unwrap();
        assert!(
            header_comment.starts_with("# lob profiles:"),
            "{header_comment}"
        );
        assert!(
            header_comment.contains("repeat_window_ms="),
            "{header_comment}"
        );
        assert!(header_comment.contains("seed="), "{header_comment}");
        assert!(
            header_comment.contains("fill_model=none"),
            "без модели исполнения шапка обязана назвать источник: {header_comment}"
        );
        assert!(
            !header_comment.contains("debug"),
            "боевой прогон не несёт debug"
        );

        // Полнота: маргинал инструмента ZEC с нулём наблюдений (сессия
        // недоступна отдельно от SOL в этой фикстуре — здесь просто
        // проверяем, что строка вообще есть, а не отфильтрована).
        assert!(
            text.contains("marginal:instrument=ZECUSDT,"),
            "профиль без выигрыша обязан остаться строкой"
        );
        // Непригодная корзина ZEC (покрытие 4 bps: [10,25) требует бы 25 bps
        // видимой книги) обязана отсутствовать вовсе, а не печататься нулём;
        // ближняя [0,1) при том же покрытии пригодна и обязана присутствовать.
        assert!(
            !text.contains("cross:ZECUSDT|eaten|[10,25)"),
            "непригодная корзина расстояния обязана отсутствовать, а не быть строкой с нулём"
        );
        assert!(
            text.contains("cross:ZECUSDT|eaten|[0,1)"),
            "ближняя корзина пригодна даже при узком покрытии ZEC"
        );
        assert!(
            text.contains("cross:SOLUSDT|eaten|[10,25)"),
            "у SOL широкое покрытие — дальний крест обязан присутствовать"
        );
        assert!(
            text.contains("not_measured"),
            "флаг unreachable без живого отчёта G-LAT печатает not_measured"
        );

        // Боевой режим пишет журнал на каждый прогон (append, не перезапись —
        // `runs::append_run_row`), поэтому после двух прогонов строк вдвое
        // больше, чем строк сетки; по модулю числа прогонов — ровно по
        // строке на id сетки.
        let rows = crate::lob::runs::read_run_rows(&args.runs_out).unwrap();
        assert!(!rows.is_empty(), "боевой режим обязан писать runs.csv");
        assert_eq!(
            rows.len(),
            2 * first.rows,
            "по строке журнала на каждый id сетки, на каждый из двух прогонов"
        );
    }

    /// `--allow-unverified`: сессия без маркера сверки всё равно считается,
    /// шапка несёт `debug`, `runs.csv` не пишется.
    #[test]
    fn allow_unverified_marks_debug_and_skips_runs_csv() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        let frames = super::super::test_support::three_level_frames();
        write_session_dir(
            root,
            "2026-09-08T020000Z",
            "SOLUSDT",
            "2026-09-08T02:00:00Z",
            2,
            false,
            &frames,
        );

        let out = root.join("profiles.csv");
        let mut args = base_args(root, candidates_csv, out.clone());
        args.allow_unverified = true;

        let summary = run_profiles(&args).expect("отладочный прогон");
        assert!(summary.debug);
        let text = std::fs::read_to_string(&out).unwrap();
        let header_comment = text.lines().next().unwrap();
        assert!(header_comment.ends_with(" debug"), "{header_comment}");
        assert!(
            text.contains("marginal:instrument=SOLUSDT,4,"),
            "неверифицированная сессия обязана засчитаться под --allow-unverified: {text}"
        );
        assert!(
            !args.runs_out.exists(),
            "отладочный режим не создаёт и не трогает runs.csv"
        );
    }

    /// Ремонт по ревью (BLOCKING, ось Манифест): без модели исполнения
    /// `fill`/`net_fill`/`net_fill_lower` обязаны печатать литерал
    /// `not_measured` **в каждой строке**, а не правдоподобное число из
    /// плейсхолдера `filled = true` — числовой `fill=1.0000`/`net_fill ==
    /// net` без модели исполнения есть выдуманный факт.
    #[test]
    fn fill_columns_print_not_measured_for_every_row_without_a_fill_model() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        let frames = super::super::test_support::three_level_frames();
        write_session_dir(
            root,
            "2026-09-08T020000Z",
            "SOLUSDT",
            "2026-09-08T02:00:00Z",
            2,
            true,
            &frames,
        );
        let out = root.join("profiles.csv");
        let args = base_args(root, candidates_csv, out.clone());
        run_profiles(&args).expect("прогон без модели исполнения");

        let mut r = csv::ReaderBuilder::new()
            .comment(Some(b'#'))
            .from_path(&out)
            .expect("файл читается");
        let mut rows = 0usize;
        for row in r.records() {
            let row = row.expect("строка читается");
            rows += 1;
            assert_eq!(row.get(22), Some("not_measured"), "fill: {row:?}");
            assert_eq!(row.get(23), Some("not_measured"), "net_fill: {row:?}");
            assert_eq!(row.get(24), Some("not_measured"), "net_fill_lower: {row:?}");
        }
        assert!(rows > 0, "таблица не пуста");
    }

    /// Тело CSV без `#`-комментариев шапки — сравнение строк без учёта
    /// изменчивых счётчиков окна (`sessions=`/`sessions_outside_window=`).
    fn csv_body_without_header_comments(text: &str) -> String {
        text.lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Критерий приёмки ticket 21 (R57), буквально: добавление сессии за
    /// пределами окна не меняет ни одной строки таблицы. Окно — узкая
    /// граница `[2026-05-01, 2026-05-02]` из файла предрегистрации; первая
    /// сессия внутри окна, вторая (добавленная между прогонами) — на
    /// 2026-06-15, далеко снаружи. `sessions_outside_window` в шапке
    /// поднимается с 0 до 1, но ни одна строка данных не сдвигается.
    #[test]
    fn session_outside_window_does_not_change_any_row() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        let preregistration = root.join("preregistration.md");
        std::fs::write(
            &preregistration,
            "exploratory: 2026-05-01\nconfirmatory: 2026-05-02\n",
        )
        .unwrap();

        let frames = super::super::test_support::three_level_frames();
        write_session_dir(
            root,
            "2026-05-01T020000Z",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
            2,
            true,
            &frames,
        );

        let mut args = base_args(root, candidates_csv, root.join("profiles.csv"));
        args.preregistration = Some(preregistration.clone());
        args.now_utc = Some("2026-06-20T00:00:00Z".to_string());

        run_profiles(&args).expect("первый прогон, окно уже заморожено файлом");
        let text_before = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
        let header_before = text_before.lines().find(|l| l.starts_with("# window:"));
        assert!(
            header_before.is_some_and(|l| l.contains("sessions_outside_window=0")),
            "{text_before}"
        );

        // Сессия далеко за пределами окна, добавленная между прогонами.
        write_session_dir(
            root,
            "2026-06-15T020000Z",
            "SOLUSDT",
            "2026-06-15T02:00:00Z",
            3,
            true,
            &frames,
        );
        run_profiles(&args).expect("второй прогон, окно то же самое");
        let text_after = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
        let header_after = text_after.lines().find(|l| l.starts_with("# window:"));
        assert!(
            header_after.is_some_and(|l| l.contains("sessions_outside_window=1")),
            "сессия вне окна обязана быть учтена в счётчике, не прочитана: {text_after}"
        );

        assert_eq!(
            csv_body_without_header_comments(&text_before),
            csv_body_without_header_comments(&text_after),
            "сессия за пределами окна не обязана менять ни одну строку таблицы"
        );
    }

    /// Датированная часть в уже существующий каталог сессии (таск 22/23):
    /// `<SYMBOL>-<день>[-pN].binlog` рядом с прежними, `session.json` не
    /// трогается — его пишет вызывающий.
    fn write_part_binlog(
        dir: &Path,
        symbol: &str,
        day: &str,
        part: u32,
        frames: &[Vec<crate::binlog::Record>],
    ) {
        let header = crate::binlog::Header {
            tick_e9: super::super::test_support::FIX_TICK_E9,
            step_e9: 1_000_000,
            max_records_per_frame: 4096,
        };
        let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
        for f in frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        let path = crate::commands::record::day_file_path(dir, symbol, day, part);
        std::fs::write(path, w.into_inner()).unwrap();
    }

    /// Один каталог `--root/<id>` с частями за двое суток — так выглядит
    /// каталог, в который владелец гоняет `lob session` день за днём:
    /// верхний `started_utc`/`start_hour_utc` — от последней сессии (D+1,
    /// 14 ч), `binlog_files` перечисляет обе части с их `started_utc`.
    fn write_two_day_session_dir(
        root: &Path,
        session_id: &str,
        symbol: &str,
        frames: &[Vec<crate::binlog::Record>],
    ) {
        let dir = root.join(session_id);
        std::fs::create_dir_all(&dir).unwrap();
        write_part_binlog(&dir, symbol, "2026-05-01", 1, frames);
        write_part_binlog(&dir, symbol, "2026-05-02", 1, frames);
        let json = format!(
            "{{\"started_utc\":\"2026-05-02T14:00:00Z\",\"start_hour_utc\":14,\
             \"instruments\":[\"{symbol}\"],\"binlog_files\":[\
             {{\"symbol\":\"{symbol}\",\"part\":1,\"started_utc\":\"2026-05-01T02:00:00Z\"}},\
             {{\"symbol\":\"{symbol}\",\"part\":1,\"started_utc\":\"2026-05-02T14:00:00Z\"}}]}}"
        );
        std::fs::write(dir.join("session.json"), json).unwrap();
        std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok").unwrap();
    }

    fn column(text: &str, name: &str) -> Vec<String> {
        let mut r = csv::ReaderBuilder::new()
            .comment(Some(b'#'))
            .from_reader(text.as_bytes());
        let idx = r
            .headers()
            .unwrap()
            .iter()
            .position(|h| h == name)
            .unwrap_or_else(|| panic!("колонки {name} нет"));
        r.records()
            .map(|row| row.unwrap().get(idx).unwrap().to_string())
            .collect()
    }

    /// Критерий приёмки таска 23: каталог с частями за двое суток даёт
    /// `G = 2` и оба часа старта — по части, не по каталогу (до таска 23
    /// обе части шли за 2026-05-02 / 14 ч из верхнего `session.json`, и
    /// `g` был бы 1).
    #[test]
    fn two_day_session_dir_yields_g_two_and_hours_per_part() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        let frames = super::super::test_support::three_level_frames();
        write_two_day_session_dir(root, "collect", "SOLUSDT", &frames);

        let args = base_args(root, candidates_csv, root.join("profiles.csv"));
        run_profiles(&args).expect("прогон по каталогу с двумя сутками");
        let text = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
        let g = column(&text, "g");
        let n = column(&text, "n");
        let hours = column(&text, "session_start_hours_utc");
        assert!(!g.is_empty(), "таблица не пуста:\n{text}");
        // Строки, куда попали наблюдения (`n > 0`), видят обе части — по
        // одной на сутки — и оба часа старта.
        let populated: Vec<usize> = n
            .iter()
            .enumerate()
            .filter(|(_, n)| n.parse::<u64>().unwrap_or(0) > 0)
            .map(|(i, _)| i)
            .collect();
        assert!(!populated.is_empty(), "есть строки с наблюдениями:\n{text}");
        for i in populated {
            assert_eq!(g[i], "2", "g строки {i}: двое суток — два кластера\n{text}");
            assert_eq!(
                hours[i], "2,14",
                "часы старта строки {i} — по части, не 14 из каталога\n{text}"
            );
        }
    }

    /// Критерий приёмки таска 23: окно «сейчас» с границей между сутками
    /// одного каталога читает только части нужных суток. Окно
    /// `[2026-05-01, 2026-05-01]` на каталоге с частями за 01 и 02 мая даёт
    /// ту же таблицу, что каталог только с частью за 01 мая; в шапке —
    /// `sessions=1 sessions_outside_window=1`.
    #[test]
    fn now_window_boundary_inside_one_directory_reads_only_the_days_in_window() {
        let frames = super::super::test_support::three_level_frames();
        let preregistration_text = "exploratory: 2026-05-01\nconfirmatory: 2026-05-01\n";

        // Эталон: каталог только с частью за 01 мая.
        let one = tempfile::tempdir().unwrap();
        let one_root = one.path();
        write_instruments_csv(one_root, &[("SOLUSDT", 5)]);
        let one_candidates = one_root.join("candidates.csv");
        write_candidates_csv(&one_candidates, &[("SOLUSDT", 300.0)]);
        write_session_dir(
            one_root,
            "collect",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
            2,
            true,
            &frames,
        );
        let one_prereg = one_root.join("preregistration.md");
        std::fs::write(&one_prereg, preregistration_text).unwrap();
        let mut one_args = base_args(one_root, one_candidates, one_root.join("profiles.csv"));
        one_args.preregistration = Some(one_prereg);
        one_args.now_utc = Some("2026-06-20T00:00:00Z".to_string());
        run_profiles(&one_args).expect("эталонный прогон");
        let expected = std::fs::read_to_string(one_args.out.clone().unwrap()).unwrap();

        // Проверяемое: тот же каталог плюс часть за 02 мая, окно — только 01.
        let two = tempfile::tempdir().unwrap();
        let two_root = two.path();
        write_instruments_csv(two_root, &[("SOLUSDT", 5)]);
        let two_candidates = two_root.join("candidates.csv");
        write_candidates_csv(&two_candidates, &[("SOLUSDT", 300.0)]);
        write_two_day_session_dir(two_root, "collect", "SOLUSDT", &frames);
        let two_prereg = two_root.join("preregistration.md");
        std::fs::write(&two_prereg, preregistration_text).unwrap();
        let mut two_args = base_args(two_root, two_candidates, two_root.join("profiles.csv"));
        two_args.preregistration = Some(two_prereg);
        two_args.now_utc = Some("2026-06-20T00:00:00Z".to_string());
        run_profiles(&two_args).expect("прогон с границей окна внутри каталога");
        let actual = std::fs::read_to_string(two_args.out.clone().unwrap()).unwrap();

        let window_line = actual
            .lines()
            .find(|l| l.starts_with("# window:"))
            .expect(&actual);
        assert!(
            window_line.contains("sessions=1 sessions_outside_window=1"),
            "часть за 02 мая — сессионная единица вне окна: {window_line}"
        );
        assert_eq!(
            csv_body_without_header_comments(&actual),
            csv_body_without_header_comments(&expected),
            "часть за сутки вне окна не обязана менять ни одну строку таблицы"
        );
        assert!(
            column(&actual, "g").iter().all(|g| g == "1" || g == "0"),
            "внутри окна — одни сутки:\n{actual}"
        );
    }

    /// Шапка `profiles-<дата>.csv` несёт `window: …` с границами, длиной в
    /// сутках (из границ файла, не из числа сессий) и парой разведочная/
    /// подтверждающая, из которой окно выведено.
    #[test]
    fn profiles_header_prints_window_line() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
        let preregistration = root.join("preregistration.md");
        std::fs::write(
            &preregistration,
            "exploratory: 2026-05-01,2026-05-02\nconfirmatory: 2026-05-03,2026-05-04\n",
        )
        .unwrap();
        let frames = super::super::test_support::three_level_frames();
        write_session_dir(
            root,
            "2026-05-01T020000Z",
            "SOLUSDT",
            "2026-05-01T02:00:00Z",
            2,
            true,
            &frames,
        );

        let mut args = base_args(root, candidates_csv, root.join("profiles.csv"));
        args.preregistration = Some(preregistration);
        run_profiles(&args).expect("боевой прогон с окном");

        let text = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
        let window_line = text
            .lines()
            .find(|l| l.starts_with("# window:"))
            .expect(&text);
        assert!(
            window_line.contains("window: 2026-05-01..2026-05-04 days=4"),
            "{window_line}"
        );
        assert!(
            window_line.contains("exploratory=2026-05-01..2026-05-02"),
            "{window_line}"
        );
        assert!(
            window_line.contains("confirmatory=2026-05-03..2026-05-04"),
            "{window_line}"
        );
    }

    /// Критерий приёмки ticket 21: без `--preregistration` и без
    /// `--allow-unverified` окно не определено — громкая ошибка, а не тихое
    /// «все сессии».
    #[test]
    fn missing_preregistration_without_allow_unverified_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_instruments_csv(root, &[("SOLUSDT", 5)]);
        let candidates_csv = root.join("candidates.csv");
        write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);

        let mut args = base_args(root, candidates_csv, root.join("profiles.csv"));
        args.preregistration = None;

        let err = run_profiles(&args).expect_err("окно не определено без файла предрегистрации");
        assert!(err.to_string().contains("--preregistration"), "{err}");
    }

    /// `PLAN.md` §11 п.5: зеркальная пара (бид/аск с противоположным
    /// движением середины) даёт одинаковый `m` (полярность по стороне это и
    /// обеспечивает), но противоположное по знаку сырое движение — «без
    /// нормировки знаком» значит без применения `σ`, не «без знака вовсе».
    /// Ремонт по ревью (BLOCKING, ось Данные): `.abs()` на `raw_vals` убран.
    #[test]
    fn mirrored_moves_give_equal_m_and_opposite_signed_raw() {
        fn level(side: crate::book::Side, death_ms: i64) -> LevelRecord {
            LevelRecord {
                side,
                price_tick: 1000,
                birth_ms: 0,
                death_ms,
                lifetime_ms: death_ms,
                size_max: 200,
                time_to_max_ms: 0,
                size_monotonic: true,
                repeat_count: 0,
                repriced: false,
                death: crate::lob::levels::DeathKind::BelowFraction,
                traded_lots: 0,
            }
        }
        fn sample(ts_ms: i64, mid2x: i64) -> MidSample {
            let bid_tick = mid2x / 2;
            let ask_tick = mid2x - bid_tick;
            MidSample {
                ts_ms,
                bid_tick,
                ask_tick,
            }
        }

        // База 20000 (удвоенная середина); бид уходит вниз на срезе 100 мс,
        // аск — на ту же величину вверх: зеркальная пара `markout.rs`.
        let bid_mids = vec![sample(0, 20_000), sample(100, 19_800)];
        let ask_mids = vec![sample(0, 20_000), sample(100, 20_200)];
        let bid_level = level(crate::book::Side::Bid, 50);
        let ask_level = level(crate::book::Side::Ask, 50);

        let m_bid = markouts_for_level(&bid_level, &bid_mids);
        let m_ask = markouts_for_level(&ask_level, &ask_mids);
        let base_bid = base_before(&bid_mids, bid_level.death_ms);
        let base_ask = base_before(&ask_mids, ask_level.death_ms);

        let mut agg_bid = ProfileAgg::default();
        let mut agg_ask = ProfileAgg::default();
        apply_observation(
            &mut agg_bid,
            bid_level.outcome(),
            m_bid,
            base_bid,
            &bid_mids,
            0,
            0,
            "SOLUSDT",
            &bid_level,
            &NoFillModel,
        );
        apply_observation(
            &mut agg_ask,
            ask_level.outcome(),
            m_ask,
            base_ask,
            &ask_mids,
            0,
            0,
            "SOLUSDT",
            &ask_level,
            &NoFillModel,
        );

        let m_bid_100 = agg_bid.horizons[0].m_obs[0].net_bps;
        let m_ask_100 = agg_ask.horizons[0].m_obs[0].net_bps;
        assert!(
            (m_bid_100 - m_ask_100).abs() < 1e-9,
            "зеркальная пара обязана дать одинаковый m: {m_bid_100} vs {m_ask_100}"
        );

        let raw_bid_100 = agg_bid.horizons[0].raw_vals[0];
        let raw_ask_100 = agg_ask.horizons[0].raw_vals[0];
        assert!(
            raw_bid_100 < 0.0 && raw_ask_100 > 0.0,
            "сырое движение обязано остаться знаковым: bid={raw_bid_100} ask={raw_ask_100}"
        );
        assert!(
            (raw_bid_100 + raw_ask_100).abs() < 1e-9,
            "величины противоположны и равны по модулю: {raw_bid_100} vs {raw_ask_100}"
        );
    }

    /// Таск 16: `observed_sharpe` — Шарп **только исполнившихся** наблюдений
    /// (`filled == true`), посчитанный вручную по трём известным числам, не
    /// вызовом `sharpe_ratio` (формула не подтверждает сама себя). Четвёртое
    /// наблюдение (`filled == false`) обязано быть исключено.
    #[test]
    fn observed_sharpe_column_matches_hand_computed_sharpe_of_filled_observations() {
        let mut agg = ProfileAgg {
            eaten: 3,
            ..ProfileAgg::default()
        };
        agg.fill_obs = vec![
            FillObservation {
                day_cluster: 0,
                net_bps: 10.0,
                filled: true,
            },
            FillObservation {
                day_cluster: 0,
                net_bps: -2.0,
                filled: true,
            },
            FillObservation {
                day_cluster: 1,
                net_bps: 999.0,
                filled: false,
            },
            FillObservation {
                day_cluster: 1,
                net_bps: 6.0,
                filled: true,
            },
        ];
        let filled = [10.0_f64, -2.0, 6.0];
        let mean = filled.iter().sum::<f64>() / 3.0;
        let var = filled.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / 3.0;
        let expected_sharpe = mean / var.sqrt();

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("row.csv");
        {
            let file = std::fs::File::create(&path).unwrap();
            let mut w = csv::WriterBuilder::new()
                .has_headers(false)
                .from_writer(file);
            write_row(&mut w, "marginal:test", &agg, true).unwrap();
            w.flush().unwrap();
        }
        let text = std::fs::read_to_string(&path).unwrap();
        let cols: Vec<&str> = text.trim_end().split(',').collect();
        let printed: f64 = cols
            .last()
            .expect("строка не пуста")
            .parse()
            .expect("observed_sharpe обязан быть числом при measured=true");
        assert!(
            (printed - expected_sharpe).abs() < 1e-6,
            "printed={printed} expected={expected_sharpe}"
        );
    }

    /// Без активной модели (`measured=false`) `observed_sharpe` печатает
    /// `not_measured` буквально, тем же приёмом, что `fill`/`net_fill` —
    /// критерий приёмки таска 16, а не молчаливый `none`.
    #[test]
    fn observed_sharpe_is_not_measured_without_a_fill_model() {
        let agg = ProfileAgg {
            eaten: 1,
            fill_obs: vec![FillObservation {
                day_cluster: 0,
                net_bps: 5.0,
                filled: true,
            }],
            ..ProfileAgg::default()
        };
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("row.csv");
        {
            let file = std::fs::File::create(&path).unwrap();
            let mut w = csv::WriterBuilder::new()
                .has_headers(false)
                .from_writer(file);
            write_row(&mut w, "marginal:test", &agg, false).unwrap();
            w.flush().unwrap();
        }
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.trim_end().ends_with("not_measured"), "{text}");
    }

    /// `resolve_fill_model` (таск 16): тройка RTT/лота задаётся вся или не
    /// задаётся вовсе — частичная тройка отказывает, а не молча берёт
    /// `NoFillModel` или изобретает недостающее число.
    #[test]
    fn resolve_fill_model_requires_all_three_or_none() {
        assert_eq!(
            resolve_fill_model(None, None, None).unwrap().label(),
            "none"
        );
        assert_eq!(
            resolve_fill_model(Some(1_000_000), Some(2_000_000), Some(100_000_000))
                .unwrap()
                .label(),
            "backtest"
        );
        assert!(resolve_fill_model(Some(1_000_000), None, None).is_err());
        assert!(resolve_fill_model(None, Some(1_000_000), Some(1)).is_err());
    }
}
