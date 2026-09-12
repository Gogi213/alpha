//! Накопление наблюдений по профилям: кластер суток (`DayIndex`), модель
//! исполнения (`FillModel`/`NoFillModel`), накопитель (`ProfileAgg`) и
//! разнос одного уровня по всем совпавшим id сетки (`accumulate_level`).
//! Отдельно от `profiles.rs`, чтобы оркестрация не тонула в механике.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;

use crate::commands::lob::{outcome_name, side_name};
use crate::lob::costs::{net_bps as cost_net_bps, FillObservation, Observation};
use crate::lob::levels::{LevelRecord, Outcome};
use crate::lob::markout::{
    base_before, future_asof, markouts_for_level, raw_return_bps, MidSample, HORIZONS_MS,
};
use crate::lob::shortlist::repeat_bucket;

use super::axes::{
    distance_bps_at_birth, distance_bucket, hour_utc_of_ms, lifetime_bucket, mid_sample_asof,
    size_bucket,
};

// ---------------------------------------------------------------------------
// Кластер суток для совместного бутстрапа: сутки — целый кластер (A1), тот
// же смысл, что везде в `stats`/`cells`/`costs`.
// ---------------------------------------------------------------------------

#[derive(Default)]
pub(super) struct DayIndex {
    ids: HashMap<String, i64>,
}

impl DayIndex {
    #[allow(clippy::cast_possible_wrap)]
    pub(super) fn id(&mut self, day_utc: &str) -> i64 {
        if let Some(&id) = self.ids.get(day_utc) {
            return id;
        }
        let id = self.ids.len() as i64;
        self.ids.insert(day_utc.to_string(), id);
        id
    }
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
pub(super) struct HorizonAgg {
    /// `net_bps` здесь — сырое подписанное `m` этого горизонта: тот же приём
    /// совместного бутстрапа с `filled = true`, что и `pilot.rs::m10s`
    /// (doc модуля) — внутренняя механика интервала, не про исполнение входа.
    pub(super) m_obs: Vec<FillObservation>,
    /// Сырое движение середины **без нормировки знаком по стороне**
    /// (`markout::raw_return_bps`, без применения `σ`) — знак сохраняется:
    /// зеркальная пара бид/аск даёт одинаковый `m`, но противоположный
    /// `raw` (`PLAN.md` §11 п.5; тест
    /// `mirrored_moves_give_equal_m_and_opposite_signed_raw`).
    pub(super) raw_vals: Vec<f64>,
}

#[derive(Default)]
pub(super) struct ProfileAgg {
    pub(super) eaten: u64,
    pub(super) pulled: u64,
    pub(super) mixed: u64,
    pub(super) horizons: [HorizonAgg; 4],
    /// `net` — средний `m` за вычетом издержек на горизонте 10 с
    /// (`costs::mean_net_bps`), без веса на исполнение.
    pub(super) net_obs: Vec<Observation>,
    /// `fill`/`net_fill` — на горизонте 10 с; наполняется только когда
    /// `FillModel::filled` возвращает `Some` (см. doc выше про
    /// `NoFillModel`/`fill_model`).
    pub(super) fill_obs: Vec<FillObservation>,
    /// Часы старта частей записи, в которых наблюдался профиль — колонка
    /// `session_start_hours_utc`. **Информация о записи, не ось** (таск 30,
    /// В-36): под олвейс-он часть одна на сутки, её час старта — полночь
    /// каждые сутки, то есть константа, и построенная на нём ось часа
    /// вырождалась в «зависимости от часа нет» при круглосуточном покрытии
    /// (`docs/findings/audit-2026-09-12.md`, «Сессии → олвейс-он» п. 1).
    /// Колонка остаётся: чем записаны сутки — 5-минутной сессией в 02 UTC
    /// или непрерывным коллектором с полуночи — читателю таблицы видно
    /// только отсюда.
    pub(super) hours: BTreeSet<u32>,
    /// Часы UTC **рождения уровней** профиля (`LevelRecord.birth_ms`) — ось
    /// «час» брифа §6а и колонка `level_hours_utc` (В-36). У сессии 5–15 мин
    /// совпадает с часом старта части с точностью до границы часа; у
    /// олвейс-она несёт настоящий разброс.
    pub(super) level_hours: BTreeSet<u32>,
    /// Годные сутки, на которые пришлось хотя бы одно наблюдение этого
    /// профиля — `G` для `shortlist::decide_profile`/подтверждающей шапки
    /// (ремонт по ревью таска 12, открытый пункт (1) для таска 13:
    /// `docs/findings/profiles-*.csv` раньше не несло числа суток на
    /// профиль, и `G` на подтверждающей был захардкожен нулём).
    pub(super) days: BTreeSet<i64>,
    /// (сутки, час рождения) → (число наблюдений, сумма `m_10s`) — вход
    /// теста на зависимость от часа суток (`shortlist::hour_dependence_test`,
    /// ticket 06/13/30). Кластер по-прежнему сутки (Decision 9,
    /// `HourDayObservation.day`), но наблюдение внутри суток — **час**:
    /// `wild_cluster_bootstrap_t` группирует по кластеру сам и принимает
    /// сколько угодно наблюдений одного кластера, а одно усреднённое
    /// значение на сутки под олвейс-оном стёрло бы весь разброс часов (все
    /// сутки дали бы средний час ≈ 11.5). Значение — среднее `m_10s` за эти
    /// сутки и час (тот же горизонт, что идёт в гейт G2 через `net_obs`).
    pub(super) hour_day_sums: BTreeMap<(i64, u32), (u32, f64)>,
}

#[allow(clippy::too_many_arguments)]
pub(super) fn apply_observation(
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
pub(super) fn accumulate_level(
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
