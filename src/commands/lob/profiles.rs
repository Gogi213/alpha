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
//! вердиктной ячейки (10 с), кластер — сутки, наблюдение внутри суток —
//! **час рождения уровня** (`LevelRecord.birth_ms`, UTC), значение —
//! среднее `m_10s` за эти сутки и час (`ProfileAgg::hour_day_sums`).
//!
//! Час рождения, а не час старта сессии (таск 30, В-36, аудит
//! `docs/findings/audit-2026-09-12.md` «Сессии → олвейс-он» п. 1): бриф §6а
//! требует показать, зависит ли профиль от часа, и писался под сессии 5–15
//! мин, где сессия целиком лежит в одном часе и «час старта» = «час
//! наблюдения». С олвейс-он коллектором (В-34) часть одна на сутки, её час
//! старта — полночь каждые сутки, ось становится константой, ковариация с
//! наблюдаемой — ноль, и тест печатал бы «зависимости от часа нет» ровно
//! тогда, когда покрытие круглосуточное. Час рождения уровня совпадает со
//! старым числом на коротких сессиях (с точностью до границы часа) и
//! остаётся осью на непрерывной записи. `session_start_hours_utc` при этом
//! из таблицы не убрана — она описывает **запись**, а не наблюдение, и в
//! тест не входит. Методический отказ теста (суток меньше
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

pub(crate) fn size_bucket(ratio: f64) -> Option<&'static str> {
    SIZE_BOUNDS
        .iter()
        .zip(SIZE_LABELS.iter())
        .find(|((lo, hi), _)| ratio >= *lo && ratio < *hi)
        .map(|(_, label)| *label)
}

pub(crate) fn lifetime_bucket(lifetime_ms: i64) -> Option<&'static str> {
    LIFETIME_BOUNDS_MS
        .iter()
        .zip(LIFETIME_LABELS.iter())
        .find(|((lo, hi), _)| lifetime_ms >= *lo && lifetime_ms < *hi)
        .map(|(_, label)| *label)
}

pub(crate) fn distance_bucket(dist_bps: f64) -> Option<&'static str> {
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
pub(crate) fn distance_bps_at_birth(mids: &[MidSample], rec: &LevelRecord) -> Option<f64> {
    let (_, mid2x) = base_before(mids, rec.birth_ms.saturating_add(1))?;
    raw_return_bps(mid2x, rec.price_tick.saturating_mul(2)).map(f64::abs)
}

/// Час UTC метки времени биржи в миллисекундах — ось «час» профиля (В-36).
///
/// Целочисленно (`div_euclid`/`rem_euclid`), как всё время в проекте (A1),
/// и тотально: `rem_euclid` даёт `[0, 24)` при любом знаке аргумента, так
/// что метка до эпохи (в данных не бывает, но арифметика не имеет права
/// зависеть от этого) не даёт отрицательного часа. Часовых поясов здесь нет
/// вовсе: биржа отдаёт UTC, и весь проект живёт в UTC.
fn hour_utc_of_ms(ts_ms: i64) -> u32 {
    const MS_PER_HOUR: i64 = 3_600_000;
    const HOURS_PER_DAY: i64 = 24;
    let hour = ts_ms.div_euclid(MS_PER_HOUR).rem_euclid(HOURS_PER_DAY);
    u32::try_from(hour).expect("rem_euclid(24) лежит в [0, 24)")
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
    /// Часы старта частей записи, в которых наблюдался профиль — колонка
    /// `session_start_hours_utc`. **Информация о записи, не ось** (таск 30,
    /// В-36): под олвейс-он часть одна на сутки, её час старта — полночь
    /// каждые сутки, то есть константа, и построенная на нём ось часа
    /// вырождалась в «зависимости от часа нет» при круглосуточном покрытии
    /// (`docs/findings/audit-2026-09-12.md`, «Сессии → олвейс-он» п. 1).
    /// Колонка остаётся: чем записаны сутки — 5-минутной сессией в 02 UTC
    /// или непрерывным коллектором с полуночи — читателю таблицы видно
    /// только отсюда.
    hours: BTreeSet<u32>,
    /// Часы UTC **рождения уровней** профиля (`LevelRecord.birth_ms`) — ось
    /// «час» брифа §6а и колонка `level_hours_utc` (В-36). У сессии 5–15 мин
    /// совпадает с часом старта части с точностью до границы часа; у
    /// олвейс-она несёт настоящий разброс.
    level_hours: BTreeSet<u32>,
    /// Годные сутки, на которые пришлось хотя бы одно наблюдение этого
    /// профиля — `G` для `shortlist::decide_profile`/подтверждающей шапки
    /// (ремонт по ревью таска 12, открытый пункт (1) для таска 13:
    /// `docs/findings/profiles-*.csv` раньше не несло числа суток на
    /// профиль, и `G` на подтверждающей был захардкожен нулём).
    days: BTreeSet<i64>,
    /// (сутки, час рождения) → (число наблюдений, сумма `m_10s`) — вход
    /// теста на зависимость от часа суток (`shortlist::hour_dependence_test`,
    /// ticket 06/13/30). Кластер по-прежнему сутки (Decision 9,
    /// `HourDayObservation.day`), но наблюдение внутри суток — **час**:
    /// `wild_cluster_bootstrap_t` группирует по кластеру сам и принимает
    /// сколько угодно наблюдений одного кластера, а одно усреднённое
    /// значение на сутки под олвейс-оном стёрло бы весь разброс часов (все
    /// сутки дали бы средний час ≈ 11.5). Значение — среднее `m_10s` за эти
    /// сутки и час (тот же горизонт, что идёт в гейт G2 через `net_obs`).
    hour_day_sums: BTreeMap<(i64, u32), (u32, f64)>,
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
    // Час старта части — про запись; ось часа — про уровень (В-36).
    let level_hour = hour_utc_of_ms(rec.birth_ms);
    agg.hours.insert(start_hour_utc);
    agg.level_hours.insert(level_hour);
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
        // горизонте 10 с, что и `net_obs` ниже (вердиктная ячейка G2), и по
        // часу рождения уровня, а не старта части (В-36).
        let hour_entry = agg
            .hour_day_sums
            .entry((day_id, level_hour))
            .or_insert((0, 0.0));
        hour_entry.0 += 1;
        hour_entry.1 += m10;
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
///
/// `level_hours_utc` (таск 30, В-36) — часы UTC рождения уровней, то есть
/// **ось** «час» (§6а брифа) и вход `hour_dependence_test`. Стоит рядом с
/// `session_start_hours_utc`, которая осталась и в осях не участвует: это
/// часы старта частей записи, под олвейс-оном — константа
/// (`ProfileAgg::hours`). Вставка в середину, а не в конец, безопасна по той
/// же причине, что и дописывание: оба читателя таблицы
/// (`shortlist::read_profile_table`, `backtest::read_table`) матчат колонки
/// по имени и лишние пропускают (их доктроки говорят это прямо), ни ту, ни
/// другую колонку часа не читают, — а `observed_sharpe` остаётся последней,
/// как её и проверяют тесты таска 16.
const HEADER: [&str; 29] = [
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
    "level_hours_utc",
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
    let level_hours: Vec<String> = agg.level_hours.iter().map(ToString::to_string).collect();
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
    record.push(level_hours.join(","));
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
            // Наблюдение — (сутки, час рождения); кластер — сутки, и одних
            // суток может быть несколько наблюдений (В-36, doc
            // `ProfileAgg::hour_day_sums`).
            let obs: Vec<HourDayObservation> = agg
                .hour_day_sums
                .iter()
                .map(|(&(day, hour), &(n, value_sum))| HourDayObservation {
                    day,
                    hour_utc: f64::from(hour),
                    value: value_sum / f64::from(n),
                })
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
mod tests;
