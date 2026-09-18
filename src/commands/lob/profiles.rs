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
//! `docs/archive/findings/audit-2026-09-12.md` «Сессии → олвейс-он» п. 1): бриф §6а
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

use std::collections::BTreeMap;
use std::io::Write as _;
use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::{LevelRecord, LevelsConfig};
use crate::lob::shortlist::{
    build_profile_grid, hour_dependence_test, load_or_write_window, log_hour_test,
    log_profile_trials, HourDayObservation,
};
use crate::stats::{BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

use super::{resolve_h3_mode, ExecutionArgs, H3Args};

mod accumulate;
mod axes;
mod coverage;
mod sessions;
mod table;

pub use accumulate::{FillModel, NoFillModel};
pub(crate) use axes::{
    distance_bps_at_birth, distance_bucket, hour_utc_of_ms, lifetime_bucket, size_bucket,
};
// Таск 37: `lob touch-profiles` читает тот же каталог сессий тем же кодом
// (пул, маркер сверки, подкаталоги, сутки для окна, метка режима `H3`) —
// ре-экспорт с видимостью крейта, сами функции не менялись.
pub(crate) use coverage::read_pool_symbols;
pub(crate) use sessions::{distinct_session_days, read_verify_marker, session_dirs};
pub(crate) use table::{format_window_line, h3_mode_label};

use accumulate::{accumulate_level, DayIndex, ProfileAgg};
use coverage::read_coverage;
use sessions::replay_one_session_day;
use table::{default_out_path, write_row, HEADER};

/// Seed совместного бутстрапа (`net_fill_interval`) — то же число, с которым
/// уже вызывает его боевой пилот (`commands::lob::pilot::run_pilot_battle`):
/// не второе изобретённое, а то же самое, напечатанное в шапке. Профили
/// касаний (таск 37) берут его отсюда — один seed на обе таблицы.
pub(crate) const BOOTSTRAP_SEED: u64 = 0;

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
        let h3_lots_value = mode
            .single_h3_lots()
            .ok_or_else(|| anyhow::anyhow!("режим H3 без единого порога в лотах (notional/strength/both) здесь не поддерживается: оси «×H3» не определены"))?;
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
