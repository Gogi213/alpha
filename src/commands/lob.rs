//! `lob <подкоманда>` — единственная точка входа для всех чисел отчёта.
//!
//! Реализована только `pick` (шаг 0.4 плана, Decision 18): отбор двух
//! инструментов для пилота. Остальные подкоманды (`record`, `watch`, `verify`,
//! `levels`, `markout`, `pilot`, `export`, `probe`, `clock`) добавляют другие
//! шаги плана — здесь стоит только то, что относится к 0.4.
//!
//! # Структура файла и почему она такая
//!
//! Часовой замер живого стакана нельзя прогнать в юнит-тесте — ему нужна
//! сеть и час времени. Поэтому Decision 18 разложен на два слоя:
//!
//! - **Чистые функции** (`build_pool`, `middle_tercile`, `prefilter_top_n`,
//!   `median_depth_per_level_usd_e9`, `select_final_two`) реализуют само
//!   правило отбора — пул, терциль, предфильтр, порог глубины, ранжирование —
//!   и не делают ввода-вывода вообще. Они принимают уже готовые данные
//!   (метаданные инструментов, обороты, измеренную глубину) и проверены
//!   тестами ниже исчерпывающе, в том числе на вырожденных входах.
//! - **Тонкая оболочка** (`measure_one_symbol`, `measure_prefiltered`,
//!   `run_pick`) ходит в сеть и час ждёт: она только собирает вход для чистых
//!   функций и не содержит собственной логики отбора. Она не покрыта тестами
//!   без сети по той же причине, по которой её нельзя устроить в CI, — и это
//!   не пробел, а прямое следствие того, что вся логика уже вынесена наружу.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::{Args, Subcommand};

use crate::bybit::rest::{
    fetch_all_linear_instruments, fetch_linear_tickers, BybitPublicRest, Instrument,
    BYBIT_MAINNET_URL,
};

// ---------------------------------------------------------------------------
// Константы Decision 18. Каждое число здесь — то самое, что названо в тексте
// решения (`PLAN.md`); ничего не подобрано по вкусу этого файла.
// ---------------------------------------------------------------------------

/// «Торгуются >= 30 суток» (Decision 18).
pub const MIN_LISTED_DAYS: i64 = 30;
const MS_PER_DAY: i64 = 24 * 60 * 60 * 1_000;

/// «Средняя треть» — терциль по обороту за 24 часа (Decision 18). Минимум
/// пула, при котором терцили вообще определены: меньше трёх записей — меньше
/// одной на треть, и деление не имеет смысла математически, а не только
/// по вкусу реализации.
pub const MIN_POOL_FOR_TERCILES: usize = 3;

/// «20 верхних по отчётному обороту» (Decision 18) — предфильтр, не критерий.
pub const PREFILTER_TOP_N: usize = 20;

/// «Один непрерывный час живого orderbook.50» (Decision 18, шаг 0.4 плана).
pub const MEASUREMENT_WINDOW_SECS: u64 = 3600;

/// «>= $2000 в номинале на уровень» (Decision 18), в фиксированной точке 1e9
/// (`ARCHITECTURE.md` A1) — тот же масштаб, что доллары нигде не участвуют
/// в сравнении гейтов, но здесь именно доллар и есть измеряемая величина.
pub const DEPTH_FLOOR_USD_E9: i64 = 2_000 * 1_000_000_000;

const LINEAR_QUOTE_COIN: &str = "USDT";
const LINEAR_CONTRACT_TYPE: &str = "LinearPerpetual";

/// Интервал пинга во время часового замера. Не число `PLAN.md` (план не
/// задаёт его нигде) и не измеренная величина — операционный выбор этого
/// файла, тот же по смыслу и по причине, что `bybit::conn::ConnConfig::
/// ping_interval` (см. его doc в `conn.rs`): значение должно укладываться
/// в документированный Bybit таймаут простоя сервера, а конкретное число
/// внутри этого окна конфигурация подключения, не гейт и не порог отчёта.
/// Названная константа в одном месте, а не литерал внутри тела
/// `measure_one_symbol`, — чтобы её было видно и менять, не читая функцию.
const MEASUREMENT_PING_INTERVAL: Duration = Duration::from_secs(20);

/// Бэкофф переподключения во время часового замера — та же природа, что и
/// `MEASUREMENT_PING_INTERVAL` выше: эксплуатационный выбор, не число плана.
const MEASUREMENT_BACKOFF: crate::bybit::conn::BackoffConfig = crate::bybit::conn::BackoffConfig {
    initial: Duration::from_millis(500),
    max: Duration::from_secs(30),
    multiplier: 2,
};

// ---------------------------------------------------------------------------
// Ошибки
// ---------------------------------------------------------------------------

/// Отказ чистой части правила Decision 18. Ни один вариант не паникует —
/// это и есть требование задачи: вырожденный вход обязан вернуть ошибку с
/// причиной, а не молча посчитать что-то похожее на ответ (тот самый дефект
/// bootstrap-модуля из `stats/mod.rs`, деливший на нулевую дисперсию).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PickError {
    /// Пул после фильтров и схлопывания дублей меньше `MIN_POOL_FOR_TERCILES`.
    /// Покрывает и пустой пул (`len == 0`): причина отказа для вызывающего
    /// одна и та же — данных не хватает для терцилей, а не что-то ещё.
    PoolTooSmallForTerciles { len: usize },
    /// Ни один измеренный кандидат не прошёл порог глубины.
    NoSurvivorsAboveDepthFloor {
        floor_usd_e9: i64,
        candidates: usize,
    },
    /// Decision 22: минимальный лот финалиста не покрывает `minNotionalValue`
    /// по последней цене. done-condition шага 0.4 требует это как условие
    /// приёмки, а не только как справочную колонку — план не описывает, что
    /// делать со следующим по рангу кандидатом в этом случае, поэтому отказ
    /// явной ошибкой честнее, чем тихая подмена на кандидата, которого
    /// правило не выбирало.
    MinNotionalNotSatisfied {
        symbol: String,
        min_order_qty_e9: i64,
        last_price_e9: i64,
        min_notional_value_e9: i64,
    },
}

impl std::fmt::Display for PickError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PickError::PoolTooSmallForTerciles { len } => write!(
                f,
                "пул из {len} инструментов меньше {MIN_POOL_FOR_TERCILES} — терцили не определены"
            ),
            PickError::NoSurvivorsAboveDepthFloor {
                floor_usd_e9,
                candidates,
            } => write!(
                f,
                "ни один из {candidates} измеренных кандидатов не набрал {} USD медианной глубины на уровень",
                *floor_usd_e9 as f64 / 1e9
            ),
            PickError::MinNotionalNotSatisfied {
                symbol,
                min_order_qty_e9,
                last_price_e9,
                min_notional_value_e9,
            } => write!(
                f,
                "{symbol}: минимальный лот {} по цене {} даёт номинал ниже minNotionalValue {} (Decision 22)",
                *min_order_qty_e9 as f64 / 1e9,
                *last_price_e9 as f64 / 1e9,
                *min_notional_value_e9 as f64 / 1e9
            ),
        }
    }
}

impl std::error::Error for PickError {}

// ---------------------------------------------------------------------------
// Стадия 1: метаданные инструмента + оборот → вход пула
// ---------------------------------------------------------------------------

/// Всё, что известно про инструмент **до** часового замера глубины: то, что
/// приносят `instruments-info` и `tickers` (`bybit::rest`), склеенные по
/// символу. «Уже измеренный кандидат», о котором говорит задача, — это он:
/// оборот уже есть, глубины ещё нет.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CandidateMeta {
    pub symbol: String,
    pub base_coin: String,
    pub quote_coin: String,
    pub contract_type: String,
    /// `None` — Bybit не прислал `launchTime` (см. `rest::Instrument`).
    pub launch_time_ms: Option<i64>,
    /// Bybit не публикует признак «токенизированная акция» ни в одном поле
    /// `instruments-info` — ни выделенного `assetClass`, ни `contractType`,
    /// который отличал бы такой контракт от обычного linear-перпа. Поэтому
    /// признак приходит явным входом (`--exclude-tokenized-equity` в
    /// `PickArgs`), а не жёстким списком тикеров внутри кода: жёсткий список
    /// был бы ровно тем «списком, который устаревает при смене листингов»,
    /// от которого Decision 18 явно уходит для терциля, и тем же изобретённым
    /// числом (точнее, набором строк), который запрещает бриф. На дату
    /// написания Bybit не листингует такие контракты на linear-perp рынке
    /// вовсе, поэтому пустой список по умолчанию — не заглушка, а факт.
    pub is_tokenized_equity: bool,
    pub turnover_24h_usd_e9: i64,
}

/// `launchTime` отсутствует у части инструментов Bybit (см.
/// `rest::Instrument::launch_time_ms`) — у тех, что заведены до появления
/// поля в API. Отсутствие в этом случае почти наверняка означает «старше
/// тридцати суток», а не «моложе»: поле появилось в API уже после того, как
/// биржа много лет публиковала перпы, так что символ без него — из той эпохи.
/// Обратная трактовка (без поля — «неизвестно, исключить») отсеивала бы
/// давно торгуемые инструменты по признаку, который не имеет отношения к их
/// возрасту, — ровно то, что Decision 18 запрещает: список, который стареет
/// и исключает не по правилу.
pub fn is_listed_long_enough(launch_time_ms: Option<i64>, now_ms: i64) -> bool {
    match launch_time_ms {
        Some(launch_ms) => (now_ms - launch_ms) / MS_PER_DAY >= MIN_LISTED_DAYS,
        None => true,
    }
}

/// Склеивает метаданные инструмента с его оборотом. Символ без тикера
/// отбрасывается — без оборота ранжировать нечем, и включать его в пул
/// значило бы дать ему явную турникету (в терциле он не мог бы быть меньше
/// последнего места, но и не должен вообще там появляться без данных).
pub fn join_candidate_meta(
    instruments: &[Instrument],
    turnover_by_symbol: &HashMap<String, i64>,
    tokenized_equity_symbols: &HashSet<String>,
) -> Vec<CandidateMeta> {
    instruments
        .iter()
        .filter_map(|inst| {
            let turnover_24h_usd_e9 = *turnover_by_symbol.get(&inst.symbol)?;
            Some(CandidateMeta {
                symbol: inst.symbol.clone(),
                base_coin: inst.base_coin.clone(),
                quote_coin: inst.quote_coin.clone(),
                contract_type: inst.contract_type.clone(),
                launch_time_ms: inst.launch_time_ms,
                is_tokenized_equity: tokenized_equity_symbols.contains(&inst.symbol),
                turnover_24h_usd_e9,
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Стадия 2: пул — фильтры + схлопывание 1000X-дублей
// ---------------------------------------------------------------------------

/// Один инструмент пула после фильтров и схлопывания дублей: то, чем
/// ранжируется терциль. Метаданные фильтров дальше не нужны — все решения
/// о допуске приняты в `build_pool`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PoolCandidate {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
}

/// Базовый актив без мультипликаторного префикса контракта. Bybit заводит
/// `1000PEPEUSDT`, `10000SATSUSDT` и т.п. как отдельные символы одного и
/// того же актива при разном размере контракта; без схлопывания один актив
/// занимал бы несколько мест в пуле и искажал терцили оборота — актив с
/// раздутым контрактом просто дублировался бы в ранжировании.
///
/// Порядок префиксов важен: длинные проверяются раньше коротких, иначе
/// `1000000BABYDOGE` ошибочно читался бы как `1000` + `000BABYDOGE`.
fn canonical_asset(base_coin: &str) -> &str {
    for prefix in ["1000000", "100000", "10000", "1000"] {
        if let Some(rest) = base_coin.strip_prefix(prefix) {
            if !rest.is_empty() {
                return rest;
            }
        }
    }
    base_coin
}

/// Строит пул Decision 18: linear USDT-перпы, торгуются >= 30 суток, не
/// токенизированная акция, дубликаты одного актива схлопнуты в один — тот,
/// у которого выше оборот (актуальный контракт этого актива), при равенстве
/// оборота — лексикографически меньший символ (детерминизм, не смысл).
pub fn build_pool(candidates: &[CandidateMeta], now_ms: i64) -> Vec<PoolCandidate> {
    let mut best: BTreeMap<&str, &CandidateMeta> = BTreeMap::new();
    for c in candidates {
        if c.quote_coin != LINEAR_QUOTE_COIN {
            continue;
        }
        if c.contract_type != LINEAR_CONTRACT_TYPE {
            continue;
        }
        if c.is_tokenized_equity {
            continue;
        }
        if !is_listed_long_enough(c.launch_time_ms, now_ms) {
            continue;
        }
        let key = canonical_asset(&c.base_coin);
        best.entry(key)
            .and_modify(|cur| {
                if c.turnover_24h_usd_e9 > cur.turnover_24h_usd_e9
                    || (c.turnover_24h_usd_e9 == cur.turnover_24h_usd_e9 && c.symbol < cur.symbol)
                {
                    *cur = c;
                }
            })
            .or_insert(c);
    }
    best.into_values()
        .map(|c| PoolCandidate {
            symbol: c.symbol.clone(),
            turnover_24h_usd_e9: c.turnover_24h_usd_e9,
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Стадия 3: терциль + предфильтр
// ---------------------------------------------------------------------------

/// Ранжирует пул по обороту (убывание; при равенстве — по символу, чтобы
/// терциль не зависел от порядка входного среза) и возвращает срез
/// отсортированных ссылок.
fn rank_by_turnover(pool: &[PoolCandidate]) -> Vec<&PoolCandidate> {
    let mut ranked: Vec<&PoolCandidate> = pool.iter().collect();
    ranked.sort_by(|a, b| {
        b.turnover_24h_usd_e9
            .cmp(&a.turnover_24h_usd_e9)
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    ranked
}

/// Треть, в которую попадает ранг `rank` (считая с нуля, по убыванию
/// оборота) среди `n` записей: `0` — верхняя треть, `1` — средняя, `2` —
/// нижняя. `rank * 3 / n` — стандартное целочисленное разбиение на `k`
/// корзин почти поровну (тот же приём, что `numpy.array_split`): размеры
/// корзин отличаются не больше чем на один элемент, а остаток `n % 3`
/// уходит в первые `n % 3` корзин по счёту — то есть в верхнюю треть, и,
/// если остаток равен двум, ещё и в среднюю; нижняя треть остатка не
/// получает никогда. Например, при `n = 20` (`20 = 3·6 + 2`) верхняя и
/// средняя трети получают по семь записей, нижняя — шесть.
fn tercile_bucket(rank: usize, n: usize) -> u8 {
    debug_assert!(n > 0 && rank < n);
    ((rank as u64 * 3) / n as u64) as u8
}

/// Средняя треть по обороту (Decision 18) — «средний эшелон» брифа: правило,
/// а не список тикеров, поэтому не устаревает со сменой листингов.
pub fn middle_tercile(pool: &[PoolCandidate]) -> Result<Vec<PoolCandidate>, PickError> {
    if pool.len() < MIN_POOL_FOR_TERCILES {
        return Err(PickError::PoolTooSmallForTerciles { len: pool.len() });
    }
    let ranked = rank_by_turnover(pool);
    let n = ranked.len();
    Ok(ranked
        .into_iter()
        .enumerate()
        .filter(|&(rank, _)| tercile_bucket(rank, n) == 1)
        .map(|(_, c)| c.clone())
        .collect())
}

/// Верхние `n` по отчётному обороту внутри терциля — предфильтр Decision 18,
/// а не критерий: он только сужает круг символов, на которые физически можно
/// подписаться в рамках лимита топиков одного часа замера, дальше решает
/// исключительно измеренная глубина.
pub fn prefilter_top_n(tercile: &[PoolCandidate], n: usize) -> Vec<PoolCandidate> {
    let mut ranked: Vec<PoolCandidate> = tercile.to_vec();
    ranked.sort_by(|a, b| {
        b.turnover_24h_usd_e9
            .cmp(&a.turnover_24h_usd_e9)
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    ranked.truncate(n);
    ranked
}

// ---------------------------------------------------------------------------
// Стадия 4: измеренная глубина — медиана по уровням, не сумма
// ---------------------------------------------------------------------------

/// Медиана целочисленной выборки. `None` на пустом входе — отсутствие
/// уровней означает «данных нет», а не «глубина ноль»: подстановка нуля
/// молча превратила бы «книга ещё не пришла» в «книга пуста», и кандидат
/// с сетевым сбоем выглядел бы как кандидат с нулевой ликвидностью.
///
/// Чётная длина усредняется через `a + (b - a) / 2`, а не `(a + b) / 2`:
/// глубины неотрицательны, поэтому `b >= a` после сортировки и `b - a` не
/// переполняет `i64` там, где `a + b` могло бы (оба значения могут быть
/// сколь угодно велики по отдельности, но их разность — нет).
pub fn median_depth_per_level_usd_e9(level_depths_usd_e9: &[i64]) -> Option<i64> {
    if level_depths_usd_e9.is_empty() {
        return None;
    }
    let mut v = level_depths_usd_e9.to_vec();
    v.sort_unstable();
    let n = v.len();
    Some(if n % 2 == 1 {
        v[n / 2]
    } else {
        let a = v[n / 2 - 1];
        let b = v[n / 2];
        a + (b - a) / 2
    })
}

/// Один замер глубины книги: момент (наносекунды от эпохи Unix — тот же
/// `local_ts_ns`, что `bybit::conn` ставит до разбора, H12) и номинал каждого
/// уровня топ-50 в USD·1e9 на этот момент, обеих сторон книги вместе. Decision
/// 18 говорит «уровней топ-50», не различая сторону — `orderbook.50`
/// публикует по 50 на сторону, и объединение сторон здесь такое же законное
/// прочтение фразы, как и разбиение по стороне; порознь план не разбивает
/// глубину по стороне ни в одном другом гейте, так что асимметричный выбор
/// (взять только бид или только аск) добавил бы решение, которого в тексте
/// нет.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DepthSample {
    pub at_ns: i64,
    pub level_notional_usd_e9: Vec<i64>,
}

/// Время-взвешенная медиана глубины по уровням за окно `[samples[0].at_ns,
/// window_end_ns)` (Decision 18: «глубина считается времени-взвешенно»).
///
/// Между двумя последовательными замерами глубина уровня считается
/// неизменной — так и есть, книга меняется только событием, а не течением
/// времени, — поэтому вклад замера в среднее взвешен длительностью до
/// следующего замера (для последнего — до конца окна). Ряды уровней у разных
/// замеров могут быть разной длины (глубина книги у края топ-50 дрожит) —
/// недостающий уровень на конкретном замере учитывается как глубина `0` на
/// этот замер, а не выбрасывается: рост числа уровней в выборке во времени
/// не должен молча отбрасывать историю замеров, где их было меньше.
///
/// **Порядок операций — тоже прочтение фразы, а не только сторона книги.**
/// «Времени-взвешенная медианная глубина» не называет порядок дословно:
/// здесь для каждой позиции уровня сперва считается взвешенное по времени
/// среднее её глубины за час (`weighted_sum[lvl] / total_weight_ns`), и уже
/// затем берётся медиана по этим ~100 усреднённым числам —
/// `median_depth_per_level_usd_e9`, которая ниже и печатает «медиана по
/// уровням, не сумма». Симметричное прочтение — на каждом замере взять
/// медиану по живым уровням этого момента (скаляр), а затем усреднить эти
/// скаляры по времени — не эквивалентно: медиана и среднее не коммутируют.
/// Выбранный порядок — тот, что явно называет Decision 18 в скобках про сам
/// порог: «$2000 в номинале на уровень (медиана **по уровням** топ-50, не
/// сумма по ним)» — внешняя операция берёт медиану по срезу уровней, то есть
/// медиана здесь одна, и она — по уровням, а не по времени; никакая
/// последовательность промежуточных медиан по времени в план не входит.
pub fn time_weighted_median_depth_usd_e9(
    samples: &[DepthSample],
    window_end_ns: i64,
) -> Option<i64> {
    let n_levels = samples
        .iter()
        .map(|s| s.level_notional_usd_e9.len())
        .max()?;
    if n_levels == 0 {
        return None;
    }
    let mut weighted_sum = vec![0i128; n_levels];
    let mut total_weight_ns: i128 = 0;
    for (i, s) in samples.iter().enumerate() {
        let next_at = samples.get(i + 1).map_or(window_end_ns, |next| next.at_ns);
        let dt = (next_at - s.at_ns).max(0) as i128;
        total_weight_ns += dt;
        for (lvl, slot) in weighted_sum.iter_mut().enumerate() {
            let depth = s.level_notional_usd_e9.get(lvl).copied().unwrap_or(0);
            *slot += depth as i128 * dt;
        }
    }
    if total_weight_ns == 0 {
        return None;
    }
    let level_avgs: Vec<i64> = weighted_sum
        .iter()
        .map(|&w| (w / total_weight_ns) as i64)
        .collect();
    median_depth_per_level_usd_e9(&level_avgs)
}

// ---------------------------------------------------------------------------
// Стадия 5: измеренный кандидат → финальные два
// ---------------------------------------------------------------------------

/// Кандидат с готовым измерением — вход последней стадии правила. Всё, что
/// нужно для ранжирования и для строки коммитимой таблицы (Decision 18,
/// done-condition шага 0.4: «окно замера» — колонка таблицы).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MeasuredCandidate {
    pub symbol: String,
    pub window_start_utc_ms: i64,
    pub window_secs: i64,
    /// Число событий книги за окно — тот же счётчик, что определяет
    /// `median_depth_usd_e9` в `time_weighted_median_depth_usd_e9` (там это
    /// `samples.len()`): каждый принятый апдейт книги — одно событие и один
    /// замер разом, отдельного счётчика заводить незачем.
    pub events: i64,
    pub median_depth_usd_e9: i64,
    /// Только для печати в таблице (Decision 18: «отчётный оборот никогда не
    /// критерий») — `select_final_two` этого поля не читает вовсе.
    pub reported_turnover_usd_e9: i64,
}

/// Сравнение по темпу событий без деления и без `f64`: `events / window_secs`
/// у `a` против того же у `b` — это `a.events * b.window_secs` против
/// `b.events * a.window_secs` (оба `window_secs > 0` по построению замера).
/// `i128`: `i64::MAX * i64::MAX ≈ 8.5·10^37` меньше `i128::MAX ≈ 1.7·10^38` —
/// произведение не переполняется на всём диапазоне `i64`, значит и на любых
/// реалистичных счётчиках событий и длительностях подавно.
fn event_rate_cmp(a: &MeasuredCandidate, b: &MeasuredCandidate) -> std::cmp::Ordering {
    (a.events as i128 * b.window_secs as i128).cmp(&(b.events as i128 * a.window_secs as i128))
}

/// Финальная стадия Decision 18: порог глубины (не сумма — уже применён на
/// стадии измерения, `median_depth_usd_e9` это медиана по построению), ранг
/// по измеренной глубине, при равенстве — по темпу событий, при равенстве и
/// там — по символу (детерминизм, не смысл). Отчётный оборот здесь не
/// участвует вовсе, поэтому не может продвинуть кандидата ниже порога.
pub fn select_final_two(
    measured: &[MeasuredCandidate],
) -> Result<Vec<MeasuredCandidate>, PickError> {
    let mut survivors: Vec<&MeasuredCandidate> = measured
        .iter()
        .filter(|m| m.median_depth_usd_e9 >= DEPTH_FLOOR_USD_E9)
        .collect();
    if survivors.is_empty() {
        return Err(PickError::NoSurvivorsAboveDepthFloor {
            floor_usd_e9: DEPTH_FLOOR_USD_E9,
            candidates: measured.len(),
        });
    }
    survivors.sort_by(|a, b| {
        b.median_depth_usd_e9
            .cmp(&a.median_depth_usd_e9)
            .then_with(|| event_rate_cmp(b, a))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    Ok(survivors.into_iter().take(2).cloned().collect())
}

/// Decision 22: минимальный лот обязан покрывать `minNotionalValue` по
/// последней цене. `minNotionalValue` — в валюте котировки (USDT),
/// `minOrderQty` — в базовом активе: без цены их не сравнить, поэтому
/// `last_price_e9` — отдельный аргумент, не поле `Instrument` (там его нет —
/// это свойство тикера в моменте, не статика контракта). `minNotionalValue
/// == 0` означает «дополнительного минимума нет» (см. `rest::Instrument::
/// min_notional_value_e9`) и тривиально выполнено.
pub fn min_lot_satisfies_min_notional(
    min_order_qty_e9: i64,
    last_price_e9: i64,
    min_notional_value_e9: i64,
) -> bool {
    if min_notional_value_e9 <= 0 {
        return true;
    }
    // Оба множителя уже в масштабе 1e9 (A1); произведение — в 1e18, обратно
    // к 1e9 делением на 1e9 — тот же приём, что `book_level_notionals_usd_e9`
    // ниже, без плавающей точки.
    let notional_e9 = (min_order_qty_e9 as i128 * last_price_e9 as i128 / 1_000_000_000) as i64;
    notional_e9 >= min_notional_value_e9
}

// ---------------------------------------------------------------------------
// Коммитимая таблица кандидатов — все промежуточные колонки (done-condition
// шага 0.4). Строится оболочкой (ниже), но сама раскладка по колонкам не
// зависит от сети, поэтому вынесена отдельно и тоже проверена тестом.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Serialize)]
pub struct CandidateRow {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
    pub tercile: &'static str,
    pub prefiltered: bool,
    pub measured: bool,
    pub window_start_utc_ms: Option<i64>,
    pub window_secs: Option<i64>,
    pub events: Option<i64>,
    pub median_depth_usd_e9: Option<i64>,
    pub above_depth_floor: Option<bool>,
    pub selected_for_pilot: bool,
    pub final_rank: Option<u8>,
}

fn tercile_label(rank: usize, n: usize) -> &'static str {
    match tercile_bucket(rank, n) {
        0 => "top",
        1 => "middle",
        _ => "bottom",
    }
}

/// Собирает полную таблицу: каждая запись пула — строка, со всеми
/// промежуточными колонками, а не только с двумя выжившими. `measured` несёт
/// только предфильтрованных (Decision 18 измеряет глубину лишь у 20 верхних
/// внутри терциля, остальным нечего печатать в колонках глубины).
pub fn build_candidate_table(
    pool: &[PoolCandidate],
    prefiltered: &[PoolCandidate],
    measured: &[MeasuredCandidate],
    selected: &[MeasuredCandidate],
) -> Vec<CandidateRow> {
    let ranked = rank_by_turnover(pool);
    let n = ranked.len();
    let prefiltered_symbols: HashSet<&str> =
        prefiltered.iter().map(|c| c.symbol.as_str()).collect();
    let measured_by_symbol: HashMap<&str, &MeasuredCandidate> =
        measured.iter().map(|m| (m.symbol.as_str(), m)).collect();
    let selected_rank: HashMap<&str, u8> = selected
        .iter()
        .enumerate()
        .map(|(i, m)| (m.symbol.as_str(), i as u8 + 1))
        .collect();

    ranked
        .into_iter()
        .enumerate()
        .map(|(rank, c)| {
            let m = measured_by_symbol.get(c.symbol.as_str()).copied();
            CandidateRow {
                symbol: c.symbol.clone(),
                turnover_24h_usd_e9: c.turnover_24h_usd_e9,
                tercile: tercile_label(rank, n),
                prefiltered: prefiltered_symbols.contains(c.symbol.as_str()),
                measured: m.is_some(),
                window_start_utc_ms: m.map(|m| m.window_start_utc_ms),
                window_secs: m.map(|m| m.window_secs),
                events: m.map(|m| m.events),
                median_depth_usd_e9: m.map(|m| m.median_depth_usd_e9),
                above_depth_floor: m.map(|m| m.median_depth_usd_e9 >= DEPTH_FLOOR_USD_E9),
                selected_for_pilot: selected_rank.contains_key(c.symbol.as_str()),
                final_rank: selected_rank.get(c.symbol.as_str()).copied(),
            }
        })
        .collect()
}

pub fn write_candidate_table_csv(path: &Path, rows: &[CandidateRow]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut w = csv::Writer::from_path(path)?;
    for row in rows {
        w.serialize(row)?;
    }
    w.flush()?;
    Ok(())
}

/// Обратное к `bybit::ws::parse_e9`: величина 1e-9 обратно в десятичную
/// строку — для человекочитаемого `instruments.csv`. Не переиспользует
/// `probe.rs::format_e9` (тот же приём, там же причина: разная точка
/// использования в чужом файле) — своя маленькая копия здесь, а тест
/// `format_e9_round_trips_through_parse_e9` ниже пином проверяет, что оба
/// файла всё равно говорят об одной и той же величине через общий `parse_e9`.
fn format_e9(v: i64) -> String {
    let neg = v < 0;
    let v = v.unsigned_abs();
    let int_part = v / 1_000_000_000;
    let frac_part = v % 1_000_000_000;
    let mut s = if frac_part == 0 {
        int_part.to_string()
    } else {
        let mut frac_str = format!("{frac_part:09}");
        while frac_str.ends_with('0') {
            frac_str.pop();
        }
        format!("{int_part}.{frac_str}")
    };
    if neg {
        s.insert(0, '-');
    }
    s
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct InstrumentRow {
    symbol: String,
    tick_size: String,
    min_order_qty: String,
    qty_step: String,
    min_notional_value: String,
}

/// `instruments.csv` (done-condition шага 0.4: «непуст», и минимальный лот
/// обоих финалистов удовлетворяет `minNotionalValue`, Decision 22) — пишется
/// для **всего** прошедшего REST пула, не только для выбранных: `lob probe`
/// и `lob record` читают эти же метаданные для любого символа, который
/// когда-либо попадёт в запись, а не только для сегодняшнего победителя.
pub fn write_instruments_csv(path: &Path, instruments: &[Instrument]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut w = csv::Writer::from_path(path)?;
    for inst in instruments {
        w.serialize(InstrumentRow {
            symbol: inst.symbol.clone(),
            tick_size: format_e9(inst.tick_e9),
            min_order_qty: format_e9(inst.min_order_qty_e9),
            qty_step: format_e9(inst.qty_step_e9),
            min_notional_value: format_e9(inst.min_notional_value_e9),
        })?;
    }
    w.flush()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Тонкая оболочка: сеть и час ожидания. Не тестируется без сети (см. doc
// модуля) — вся логика отбора уже выше, в чистых функциях.
// ---------------------------------------------------------------------------

/// Наносекунды от эпохи Unix, через `bybit::conn::SystemClock` — **не** через
/// свой `SystemTime::now().duration_since(UNIX_EPOCH).expect(...)`. Раньше
/// здесь стоял именно такой `.expect(...)`: `conn.rs::SystemClock::ns_since_epoch`
/// документирует ровно этот случай («мёртвая RTC или несконфигурированная VM
/// отдают `SystemTime::now() < UNIX_EPOCH`, и это падало на первом же
/// фрейме — рекордер... гас в первую же секунду работы») как уже однажды
/// исправленный дефект. `lob pick` не имеет права реимплементировать тот же
/// баг под другим именем — переиспользование делает второй экземпляр той же
/// ошибки невозможным по построению, а не только маловероятным.
fn wall_clock_ns() -> i64 {
    use crate::bybit::conn::Clock;
    crate::bybit::conn::SystemClock.now_ns()
}

fn wall_clock_ms() -> i64 {
    wall_clock_ns() / 1_000_000
}

/// Номинал одного уровня книги в USD·1e9, целиком в целых (A1): `tick_e9` и
/// `step_e9` — те же масштабы, что уже используются для подключения к этому
/// символу (`bybit::conn::ConnConfig`), поэтому передаются вызывающим, а не
/// читаются из `Book` — у неё нет публичного геттера этих полей, и заводить
/// его здесь означало бы менять чужой файл (`book/mod.rs`) ради этой оболочки.
fn level_notional_usd_e9(tick: i64, qty_lots: i64, tick_e9: i64, step_e9: i64) -> i64 {
    let price_e9 = tick as i128 * tick_e9 as i128;
    let qty_e9 = qty_lots as i128 * step_e9 as i128;
    // Оба множителя уже в масштабе 1e9; их произведение — в 1e18, обратно
    // к 1e9 делением на 1e9. Без плавающей точки: см. doc модуля A1 и
    // комментарий `event_rate_cmp` про пределы `i128`.
    (price_e9 * qty_e9 / 1_000_000_000) as i64
}

/// Номинал каждого уровня книги обеих сторон в USD·1e9, одним вектором и
/// одной аллокацией (`DepthSample` несёт обе стороны вместе — см. её doc).
///
/// Раньше здесь стояли два отдельных вызова этой функции (по одному на
/// сторону) плюс `Vec::extend`: бид-вектор аллоцируется под точный размер
/// бид-уровней, и `extend` аском почти всегда заставляет его перевыделяться
/// заново — три аллокации на принятое обновление книги вместо одной.
/// `Iterator::chain` вместе с `collect` избегает этого: `Book::levels`
/// отдаёт `Map<Range<usize>, _>`, чей `size_hint` точен, `Chain` складывает
/// точные `size_hint` обеих сторон, и `collect` резервирует нужный размер
/// заранее одним выделением. На часовом замере с `PREFILTER_TOP_N`
/// параллельными символами это не косметика: событие книги — самый частый
/// код этого модуля.
fn book_level_notionals_usd_e9(book: &crate::book::Book, tick_e9: i64, step_e9: i64) -> Vec<i64> {
    use crate::book::Side;
    book.levels(Side::Bid)
        .chain(book.levels(Side::Ask))
        .map(|(tick, qty_lots)| level_notional_usd_e9(tick, qty_lots, tick_e9, step_e9))
        .collect()
}

/// Один символ: подключается, копит замеры глубины до `deadline`, отдаёт их
/// оболочке выше для усреднения по времени. Разрыв последовательности `u`
/// внутри `bybit::conn::Connection` уже разрешается ресинком самим
/// соединением — здесь достаточно применять только успешные апдейты книги.
///
/// `deadline` — монотонный `Instant`, посчитанный **один раз** вызывающим
/// (`measure_prefiltered`) до того, как запущен хоть один символ, а не
/// `Instant::now() + duration` внутри этой функции: до `PREFILTER_TOP_N`
/// задач планируются и подключаются не одновременно (обычный джиттер
/// планировщика `tokio`, плюс у каждой свой бэкофф переподключения), и
/// пересчёт с нуля внутри каждой задачи растягивал бы дедлайн именно этого
/// символа на величину этой задержки — молча, без ошибки, без следа в
/// коммитимой таблице. Один и тот же `Instant` для всех задач держит их на
/// общей временной шкале с `window_end_ns`, которым дальше взвешивается
/// последний замер каждого символа (`time_weighted_median_depth_usd_e9`).
async fn measure_one_symbol(
    symbol: String,
    tick_e9: i64,
    step_e9: i64,
    deadline: tokio::time::Instant,
) -> Vec<DepthSample> {
    use crate::book::Book;
    use crate::bybit::conn::{
        BybitPublicLinearConnector, ConnConfig, ConnEvent, Connection, SystemClock,
    };
    use crate::bybit::ws::Event;

    let cfg = ConnConfig {
        symbol,
        tick_e9,
        step_e9,
        ping_interval: MEASUREMENT_PING_INTERVAL,
        backoff: MEASUREMENT_BACKOFF,
    };
    let (tx, mut rx) = tokio::sync::mpsc::channel::<ConnEvent>(4096);
    let conn = Connection::new(BybitPublicLinearConnector, cfg);
    let conn_task = tokio::spawn(conn.run(SystemClock, tx));

    let mut book = Book::new(tick_e9, step_e9);
    let mut samples = Vec::new();

    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, rx.recv()).await {
            Ok(Some(ConnEvent::Message {
                local_ts_ns,
                event: Event::Book(update),
            })) => {
                if book.apply(&update).is_ok() {
                    samples.push(DepthSample {
                        at_ns: local_ts_ns,
                        level_notional_usd_e9: book_level_notionals_usd_e9(&book, tick_e9, step_e9),
                    });
                }
            }
            Ok(Some(_)) => {}
            Ok(None) => break,
            Err(_) => break, // истекло `remaining` — окно замера кончилось
        }
    }

    conn_task.abort();
    samples
}

/// Замеряет все предфильтрованные символы одновременно (лимит топиков на
/// соединение — причина, по которой пул сузили до `PREFILTER_TOP_N` заранее,
/// Decision 18) в течение одного и того же часа, а не по очереди.
pub async fn measure_prefiltered(
    prefiltered: &[PoolCandidate],
    instruments_by_symbol: &HashMap<String, &Instrument>,
    window_secs: u64,
) -> Vec<MeasuredCandidate> {
    let window_start_utc_ms = wall_clock_ms();
    let window_start_ns = wall_clock_ns();
    let duration = Duration::from_secs(window_secs);
    let window_end_ns = window_start_ns + duration.as_nanos() as i64;
    // Один и тот же дедлайн для всех символов — см. doc `measure_one_symbol`
    // про то, почему он не пересчитывается внутри каждой задачи.
    let deadline = tokio::time::Instant::now() + duration;

    let mut handles = Vec::with_capacity(prefiltered.len());
    for cand in prefiltered {
        let Some(inst) = instruments_by_symbol.get(&cand.symbol) else {
            continue;
        };
        handles.push((
            cand.symbol.clone(),
            cand.turnover_24h_usd_e9,
            tokio::spawn(measure_one_symbol(
                cand.symbol.clone(),
                inst.tick_e9,
                inst.qty_step_e9,
                deadline,
            )),
        ));
    }

    let mut out = Vec::new();
    for (symbol, reported_turnover_usd_e9, handle) in handles {
        let Ok(samples) = handle.await else { continue };
        let Some(median_depth_usd_e9) = time_weighted_median_depth_usd_e9(&samples, window_end_ns)
        else {
            continue;
        };
        out.push(MeasuredCandidate {
            symbol,
            window_start_utc_ms,
            window_secs: window_secs as i64,
            events: samples.len() as i64,
            median_depth_usd_e9,
            reported_turnover_usd_e9,
        });
    }
    out
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Debug, Subcommand)]
pub enum LobCommand {
    /// Отбор инструментов для пилота (шаг 0.4, Decision 18).
    Pick(PickArgs),
}

/// Диспетчер подкоманд `lob` для будущего `main.rs` (пока не подключён —
/// `main.rs` не входит в этот проход). Печатает то же, что попадает в
/// коммитимую таблицу, на stdout: строку на кандидата плюс итоговых
/// финалистов, чтобы `lob pick` был полезен и без последующего чтения CSV.
pub fn dispatch(cmd: LobCommand) -> anyhow::Result<()> {
    match cmd {
        LobCommand::Pick(args) => {
            let report = run_pick(&args)?;
            for row in &report.table {
                println!(
                    "{}\t{}\t{}",
                    row.symbol,
                    row.tercile,
                    if row.selected_for_pilot {
                        "SELECTED"
                    } else {
                        ""
                    }
                );
            }
            for m in &report.selected {
                println!(
                    "pilot: {} (глубина {} USD·1e-9)",
                    m.symbol, m.median_depth_usd_e9
                );
            }
            Ok(())
        }
    }
}

#[derive(Debug, Args)]
pub struct PickArgs {
    /// REST-хост Bybit v5. Параметр, не константа — тесты и `testnet`
    /// подставляют другой (см. `bybit::rest::BybitPublicRest`).
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// Куда писать `instruments.csv` (Decision 7: рядом с записью, не в git).
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Куда писать коммитимую таблицу кандидатов (done-condition шага 0.4).
    #[arg(long, default_value = "docs/plan/candidates.csv")]
    pub candidates_out: PathBuf,
    /// Символы, вручную помеченные как токенизированные акции — см. doc
    /// `CandidateMeta::is_tokenized_equity`. Пусто по умолчанию.
    #[arg(long, value_delimiter = ',')]
    pub exclude_tokenized_equity: Vec<String>,
}

/// Итог `lob pick`: полная таблица (все промежуточные колонки) и то, что
/// в итоге назначено пилоту (`H10`: 0, 1 или 2 символа — см. doc
/// `select_final_two`, ошибка возможна и после сети, не только до неё).
pub struct PickReport {
    pub table: Vec<CandidateRow>,
    pub selected: Vec<MeasuredCandidate>,
}

/// Точка входа `lob pick`. Синхронная сигнатура по образцу
/// `probe.rs`/`rest.rs`: свой рантайм внутри, `block_on` наружу — вызывающему
/// (будущему `main.rs`) не нужно становиться асинхронным ради одной команды.
/// Многопоточный рантайм (не `current_thread`, как у `BybitPublicRest`):
/// `measure_prefiltered` держит до `PREFILTER_TOP_N` живых сокетов
/// одновременно, и разнести их по нескольким потокам дешевле, чем гонять
/// такое количество на одном.
pub fn run_pick(args: &PickArgs) -> anyhow::Result<PickReport> {
    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(run_pick_async(args))
}

async fn run_pick_async(args: &PickArgs) -> anyhow::Result<PickReport> {
    let mut rest = BybitPublicRest::new(args.base_url.clone())?;
    let instruments = fetch_all_linear_instruments(&mut rest)?;
    let tickers = fetch_linear_tickers(&mut rest)?;
    write_instruments_csv(&args.root.join("instruments.csv"), &instruments)?;

    let turnover_by_symbol: HashMap<String, i64> = tickers
        .iter()
        .map(|t| (t.symbol.clone(), t.turnover_24h_usd_e9))
        .collect();
    // Отдельная карта, не то же поле, что оборот: Decision 22 нужна текущая
    // цена, а не оборот, и обе карты строятся из одного и того же ответа
    // `tickers`, полученного ровно один раз.
    let last_price_by_symbol: HashMap<String, i64> = tickers
        .iter()
        .map(|t| (t.symbol.clone(), t.last_price_e9))
        .collect();
    let exclude: HashSet<String> = args.exclude_tokenized_equity.iter().cloned().collect();
    let meta = join_candidate_meta(&instruments, &turnover_by_symbol, &exclude);

    let now_ms = wall_clock_ms();
    let pool = build_pool(&meta, now_ms);
    let tercile = middle_tercile(&pool)?;
    let prefiltered = prefilter_top_n(&tercile, PREFILTER_TOP_N);

    let instruments_by_symbol: HashMap<String, &Instrument> =
        instruments.iter().map(|i| (i.symbol.clone(), i)).collect();
    let measured = measure_prefiltered(
        &prefiltered,
        &instruments_by_symbol,
        MEASUREMENT_WINDOW_SECS,
    )
    .await;
    let selected = select_final_two(&measured)?;

    // Decision 22, done-condition шага 0.4: у обоих финалистов минимальный
    // лот обязан удовлетворять `minNotionalValue`. Проверяется здесь, а не
    // раньше — правило Decision 18 ранжирует по глубине и обороту, а не по
    // этому условию, и незачем гонять час замера по кандидату, который потом
    // всё равно не пройдёт эту проверку, но и рано отбрасывать до измерения
    // тоже нельзя: само условие не входит в критерии отбора.
    for m in &selected {
        let Some(inst) = instruments_by_symbol.get(&m.symbol) else {
            continue;
        };
        let last_price_e9 = last_price_by_symbol.get(&m.symbol).copied().unwrap_or(0);
        if !min_lot_satisfies_min_notional(
            inst.min_order_qty_e9,
            last_price_e9,
            inst.min_notional_value_e9,
        ) {
            return Err(PickError::MinNotionalNotSatisfied {
                symbol: m.symbol.clone(),
                min_order_qty_e9: inst.min_order_qty_e9,
                last_price_e9,
                min_notional_value_e9: inst.min_notional_value_e9,
            }
            .into());
        }
    }

    let table = build_candidate_table(&pool, &prefiltered, &measured, &selected);
    write_candidate_table_csv(&args.candidates_out, &table)?;

    Ok(PickReport { table, selected })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(symbol: &str, base: &str, turnover: i64) -> CandidateMeta {
        CandidateMeta {
            symbol: symbol.to_string(),
            base_coin: base.to_string(),
            quote_coin: "USDT".to_string(),
            contract_type: "LinearPerpetual".to_string(),
            launch_time_ms: Some(0), // «родился на эпохе» — всегда старше 30 суток в тестах
            is_tokenized_equity: false,
            turnover_24h_usd_e9: turnover,
        }
    }

    const NOW_MS: i64 = 2_000_000_000_000; // произвольная точка «сейчас» для всех тестов пула

    fn e9(dollars: i64) -> i64 {
        dollars * 1_000_000_000
    }

    // -- join_candidate_meta --------------------------------------------

    fn no_data_instrument() -> Instrument {
        Instrument {
            symbol: "NODATAUSDT".to_string(),
            base_coin: "NODATA".to_string(),
            quote_coin: "USDT".to_string(),
            contract_type: "LinearPerpetual".to_string(),
            status: "Trading".to_string(),
            launch_time_ms: Some(0),
            tick_e9: 1,
            min_order_qty_e9: 1,
            qty_step_e9: 1,
            min_notional_value_e9: 0,
        }
    }

    #[test]
    fn join_drops_symbols_without_a_ticker() {
        let instruments = vec![no_data_instrument()];
        let out = join_candidate_meta(&instruments, &HashMap::new(), &HashSet::new());
        assert!(
            out.is_empty(),
            "без оборота ранжировать нечем — символ выпадает"
        );
    }

    /// Требуемый тест: символ выпадает именно из-за отсутствия тикера, а не
    /// потому что `join_candidate_meta` теряет всё подряд. Без символа, у
    /// которого тикер ЕСТЬ, тест выше не отличил бы «фильтрует по тикеру» от
    /// «возвращает пустой список независимо от входа».
    #[test]
    fn join_keeps_the_symbol_that_has_a_ticker_and_drops_the_one_that_does_not() {
        let mut has_ticker = no_data_instrument();
        has_ticker.symbol = "HASDATAUSDT".to_string();
        has_ticker.base_coin = "HASDATA".to_string();
        let instruments = vec![has_ticker, no_data_instrument()];
        let turnover = HashMap::from([("HASDATAUSDT".to_string(), e9(1_000))]);

        let out = join_candidate_meta(&instruments, &turnover, &HashSet::new());

        assert_eq!(
            out.iter().map(|c| c.symbol.as_str()).collect::<Vec<_>>(),
            vec!["HASDATAUSDT"],
            "с тикером — остаётся, без тикера — выпадает, оба в одном вызове"
        );
    }

    #[test]
    fn join_marks_the_flagged_symbol_as_tokenized_equity() {
        let instruments = vec![Instrument {
            symbol: "TSLAUSDT".to_string(),
            base_coin: "TSLA".to_string(),
            quote_coin: "USDT".to_string(),
            contract_type: "LinearPerpetual".to_string(),
            status: "Trading".to_string(),
            launch_time_ms: Some(0),
            tick_e9: 1,
            min_order_qty_e9: 1,
            qty_step_e9: 1,
            min_notional_value_e9: 0,
        }];
        let turnover = HashMap::from([("TSLAUSDT".to_string(), e9(1_000))]);
        let exclude = HashSet::from(["TSLAUSDT".to_string()]);
        let out = join_candidate_meta(&instruments, &turnover, &exclude);
        assert!(out[0].is_tokenized_equity);
    }

    // -- is_listed_long_enough -------------------------------------------

    #[test]
    fn exactly_thirty_days_old_is_long_enough() {
        let launch = NOW_MS - MIN_LISTED_DAYS * MS_PER_DAY;
        assert!(is_listed_long_enough(Some(launch), NOW_MS));
    }

    #[test]
    fn one_millisecond_short_of_thirty_days_is_not_long_enough() {
        let launch = NOW_MS - MIN_LISTED_DAYS * MS_PER_DAY + 1;
        assert!(!is_listed_long_enough(Some(launch), NOW_MS));
    }

    #[test]
    fn missing_launch_time_is_treated_as_long_listed() {
        assert!(is_listed_long_enough(None, NOW_MS));
    }

    // -- build_pool: фильтры ----------------------------------------------

    #[test]
    fn pool_excludes_non_usdt_quote() {
        let mut c = meta("BTCUSD", "BTC", e9(1_000_000));
        c.quote_coin = "USD".to_string();
        assert!(build_pool(&[c], NOW_MS).is_empty());
    }

    #[test]
    fn pool_excludes_non_linear_perpetual_contract_type() {
        let mut c = meta("BTCUSDT", "BTC", e9(1_000_000));
        c.contract_type = "LinearFutures".to_string();
        assert!(build_pool(&[c], NOW_MS).is_empty());
    }

    #[test]
    fn pool_excludes_tokenized_equity() {
        let mut c = meta("TSLAUSDT", "TSLA", e9(1_000_000));
        c.is_tokenized_equity = true;
        assert!(build_pool(&[c], NOW_MS).is_empty());
    }

    #[test]
    fn pool_excludes_recently_listed_instruments() {
        let mut c = meta("NEWUSDT", "NEW", e9(1_000_000));
        c.launch_time_ms = Some(NOW_MS - (MIN_LISTED_DAYS - 1) * MS_PER_DAY);
        assert!(build_pool(&[c], NOW_MS).is_empty());
    }

    // -- build_pool: схлопывание 1000X-дублей ------------------------------

    #[test]
    fn dedup_collapses_1000x_variant_into_the_canonical_asset() {
        let candidates = vec![
            meta("PEPEUSDT", "PEPE", e9(500)),
            meta("1000PEPEUSDT", "1000PEPE", e9(2_000)),
        ];
        let pool = build_pool(&candidates, NOW_MS);
        assert_eq!(pool.len(), 1, "один актив — одна запись в пуле");
        assert_eq!(
            pool[0].symbol, "1000PEPEUSDT",
            "выживает вариант с бОльшим оборотом"
        );
        assert_eq!(pool[0].turnover_24h_usd_e9, e9(2_000));
    }

    #[test]
    fn dedup_10000x_prefix_also_collapses() {
        let candidates = vec![
            meta("SATSUSDT", "SATS", e9(2_000)),
            meta("10000SATSUSDT", "10000SATS", e9(500)),
        ];
        let pool = build_pool(&candidates, NOW_MS);
        assert_eq!(pool.len(), 1);
        assert_eq!(
            pool[0].symbol, "SATSUSDT",
            "у него выше оборот — он и выживает"
        );
    }

    #[test]
    fn dedup_does_not_merge_unrelated_assets() {
        let candidates = vec![
            meta("PEPEUSDT", "PEPE", e9(500)),
            meta("BTCUSDT", "BTC", e9(2_000)),
        ];
        let pool = build_pool(&candidates, NOW_MS);
        assert_eq!(pool.len(), 2, "разные активы не должны схлопнуться");
    }

    #[test]
    fn dedup_tie_in_turnover_breaks_by_symbol_deterministically() {
        let candidates = vec![
            meta("1000FOOUSDT", "1000FOO", e9(500)),
            meta("FOOUSDT", "FOO", e9(500)),
        ];
        let pool = build_pool(&candidates, NOW_MS);
        assert_eq!(pool.len(), 1);
        // Лексикографически: цифра '1' меньше буквы 'F' в ASCII, поэтому при
        // точном равенстве оборота (редкий, в основном синтетический случай)
        // побеждает "1000FOOUSDT" — правило детерминировано, но не выбирает
        // «каноничный» вариант намеренно: единственная цель тай-брейка —
        // стабильность между прогонами, не выбор конкретного символа.
        assert_eq!(pool[0].symbol, "1000FOOUSDT");
    }

    // -- middle_tercile -----------------------------------------------------

    fn pool_of(pairs: &[(&str, i64)]) -> Vec<PoolCandidate> {
        pairs
            .iter()
            .map(|&(s, t)| PoolCandidate {
                symbol: s.to_string(),
                turnover_24h_usd_e9: t,
            })
            .collect()
    }

    /// Требуемый тест: терциль исключает BTC-подобную запись с максимальным
    /// оборотом и запись с минимальным оборотом длинного хвоста, оставляя
    /// средний эшелон.
    #[test]
    fn middle_tercile_excludes_top_turnover_and_bottom_turnover() {
        // Девять записей — три ровные трети (см. doc `tercile_bucket`), чтобы
        // границы бакетов совпадали с границами групп без остатка, который
        // сдвигал бы одну запись в соседнюю треть.
        let pool = pool_of(&[
            ("BTCUSDT", e9(10_000_000)), // топ, как BTC/ETH — вычёркивается по правилу
            ("ETHUSDT", e9(5_000_000)),
            ("BNBUSDT", e9(4_000_000)),
            ("MIDAUSDT", e9(500_000)),
            ("MIDBUSDT", e9(400_000)),
            ("MIDCUSDT", e9(300_000)),
            ("TAILAUSDT", e9(1_000)),
            ("TAILBUSDT", e9(500)),
            ("TAILCUSDT", e9(100)),
        ]);
        let middle = middle_tercile(&pool).unwrap();
        let symbols: HashSet<&str> = middle.iter().map(|c| c.symbol.as_str()).collect();
        assert_eq!(symbols.len(), 3);
        assert!(
            !symbols.contains("BTCUSDT"),
            "топ по обороту обязан быть исключён"
        );
        assert!(
            !symbols.contains("ETHUSDT"),
            "топ по обороту обязан быть исключён"
        );
        assert!(
            !symbols.contains("BNBUSDT"),
            "топ по обороту обязан быть исключён"
        );
        assert!(
            !symbols.contains("TAILCUSDT"),
            "хвост по обороту обязан быть исключён"
        );
        assert!(
            symbols.contains("MIDAUSDT"),
            "средний эшелон обязан остаться"
        );
        assert!(symbols.contains("MIDBUSDT"));
        assert!(symbols.contains("MIDCUSDT"));
    }

    #[test]
    fn middle_tercile_errors_on_empty_pool() {
        assert_eq!(
            middle_tercile(&[]).unwrap_err(),
            PickError::PoolTooSmallForTerciles { len: 0 }
        );
    }

    #[test]
    fn middle_tercile_errors_on_a_pool_of_two() {
        let pool = pool_of(&[("A", e9(1)), ("B", e9(2))]);
        assert_eq!(
            middle_tercile(&pool).unwrap_err(),
            PickError::PoolTooSmallForTerciles { len: 2 }
        );
    }

    #[test]
    fn middle_tercile_never_panics_on_a_pool_of_one() {
        let pool = pool_of(&[("A", e9(1))]);
        assert_eq!(
            middle_tercile(&pool).unwrap_err(),
            PickError::PoolTooSmallForTerciles { len: 1 },
            "тот же вариант ошибки, что и для пула из двух — не паника и не другой вариант"
        );
    }

    #[test]
    fn middle_tercile_of_exactly_three_keeps_the_single_middle_entry() {
        let pool = pool_of(&[("TOP", e9(300)), ("MID", e9(200)), ("BOT", e9(100))]);
        let middle = middle_tercile(&pool).unwrap();
        assert_eq!(middle.len(), 1);
        assert_eq!(middle[0].symbol, "MID");
    }

    #[test]
    fn middle_tercile_result_does_not_depend_on_input_order() {
        let sorted = pool_of(&[
            ("A", e9(600)),
            ("B", e9(500)),
            ("C", e9(400)),
            ("D", e9(300)),
            ("E", e9(200)),
            ("F", e9(100)),
        ]);
        let mut shuffled = sorted.clone();
        shuffled.reverse();
        let m1: HashSet<String> = middle_tercile(&sorted)
            .unwrap()
            .into_iter()
            .map(|c| c.symbol)
            .collect();
        let m2: HashSet<String> = middle_tercile(&shuffled)
            .unwrap()
            .into_iter()
            .map(|c| c.symbol)
            .collect();
        assert_eq!(m1, m2);
    }

    #[test]
    fn middle_tercile_ties_in_turnover_break_by_symbol_deterministically() {
        // Шесть записей с одинаковым оборотом: терциль обязан быть стабилен
        // по алфавиту, а не по порядку появления во входном срезе.
        let pool = pool_of(&[
            ("F", e9(100)),
            ("D", e9(100)),
            ("B", e9(100)),
            ("A", e9(100)),
            ("C", e9(100)),
            ("E", e9(100)),
        ]);
        let middle = middle_tercile(&pool).unwrap();
        let symbols: Vec<&str> = middle.iter().map(|c| c.symbol.as_str()).collect();
        assert_eq!(
            symbols,
            vec!["C", "D"],
            "средняя треть алфавита при равном обороте"
        );
    }

    // -- prefilter_top_n -----------------------------------------------------

    #[test]
    fn prefilter_takes_top_n_by_turnover() {
        let pool = pool_of(&[("A", e9(3)), ("B", e9(1)), ("C", e9(2))]);
        let top = prefilter_top_n(&pool, 2);
        assert_eq!(
            top.iter().map(|c| c.symbol.clone()).collect::<Vec<_>>(),
            vec!["A", "C"]
        );
    }

    #[test]
    fn prefilter_returns_everything_when_fewer_than_n() {
        let pool = pool_of(&[("A", e9(1))]);
        let top = prefilter_top_n(&pool, 20);
        assert_eq!(top.len(), 1);
        assert_eq!(
            top[0], pool[0],
            "единственный кандидат обязан пройти невредимым, не просто числом один"
        );
    }

    #[test]
    fn prefilter_of_empty_tercile_is_empty_not_a_panic() {
        assert!(prefilter_top_n(&[], 20).is_empty());
    }

    // -- median_depth_per_level_usd_e9 --------------------------------------

    #[test]
    fn median_of_odd_length_is_the_middle_value() {
        let levels = vec![e9(10), e9(30), e9(20)];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(20)));
    }

    #[test]
    fn median_of_even_length_averages_the_two_middle_values() {
        let levels = vec![e9(10), e9(20), e9(30), e9(40)];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(25)));
    }

    /// Все уровни одной глубины — книга без формы, вырожденный, но реальный
    /// случай (например, все 50 уровней у минимального лота). Медиана обязана
    /// вернуть ровно эту глубину, не среднее с искажением от `a + (b-a)/2`.
    #[test]
    fn median_of_all_equal_values_returns_that_value() {
        let levels = vec![e9(7); 50];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(7)));
    }

    #[test]
    fn median_of_empty_input_is_none_not_a_panic() {
        assert_eq!(median_depth_per_level_usd_e9(&[]), None);
    }

    #[test]
    fn median_of_a_single_level_is_that_level() {
        assert_eq!(median_depth_per_level_usd_e9(&[e9(42)]), Some(e9(42)));
    }

    /// Требуемый тест: медиана и сумма расходятся, и порог обязан применяться
    /// к медиане. Один толстый уровень ($50000) плюс 49 тонких ($10 каждый):
    /// сумма перескакивает порог $2000 с большим запасом, а медиана — нет,
    /// потому что реальная ликвидность у 49 из 50 уровней ничтожна.
    #[test]
    fn median_disagrees_with_sum_and_the_floor_must_use_the_median() {
        let mut levels = vec![e9(10); 49];
        levels.push(e9(50_000));
        let sum: i64 = levels.iter().sum();
        let median = median_depth_per_level_usd_e9(&levels).unwrap();

        assert_eq!(sum, e9(10) * 49 + e9(50_000));
        assert!(sum >= DEPTH_FLOOR_USD_E9, "сумма прошла бы порог");
        assert_eq!(
            median,
            e9(10),
            "медиана — типичный, а не выдающийся уровень"
        );
        assert!(
            median < DEPTH_FLOOR_USD_E9,
            "медиана обязана провалить порог"
        );
    }

    /// Крупные значения — без переполнения при усреднении двух средних:
    /// `a + (b - a) / 2`, а не `(a + b) / 2`.
    #[test]
    fn median_does_not_overflow_on_the_largest_i64_values() {
        let levels = vec![i64::MAX - 2, i64::MAX];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(i64::MAX - 1));
        let levels_eq = vec![i64::MAX, i64::MAX];
        assert_eq!(median_depth_per_level_usd_e9(&levels_eq), Some(i64::MAX));
    }

    // -- time_weighted_median_depth_usd_e9 -----------------------------------

    #[test]
    fn time_weighted_median_weighs_by_duration_to_next_sample() {
        // Один уровень: глубина 10 держится 1с, потом 30 держится 3с — среднее
        // взвешенное (10*1 + 30*3)/4 = 25, не простое среднее (10+30)/2=20.
        let samples = vec![
            DepthSample {
                at_ns: 0,
                level_notional_usd_e9: vec![e9(10)],
            },
            DepthSample {
                at_ns: 1_000_000_000,
                level_notional_usd_e9: vec![e9(30)],
            },
        ];
        let window_end_ns = 4_000_000_000;
        assert_eq!(
            time_weighted_median_depth_usd_e9(&samples, window_end_ns),
            Some(e9(25))
        );
    }

    #[test]
    fn time_weighted_median_of_empty_samples_is_none_not_a_panic() {
        assert_eq!(time_weighted_median_depth_usd_e9(&[], 1_000), None);
    }

    #[test]
    fn time_weighted_median_handles_ragged_level_counts_without_panicking() {
        let samples = vec![
            DepthSample {
                at_ns: 0,
                level_notional_usd_e9: vec![e9(10), e9(20)],
            },
            DepthSample {
                at_ns: 1,
                level_notional_usd_e9: vec![e9(30)], // на один уровень короче
            },
        ];
        // Ручной расчёт по документированному правилу («недостающий уровень
        // считается глубиной 0 на этот замер»), window_end_ns = 2:
        //   lvl0: (10*1 + 30*1) / 2 = 20   lvl1: (20*1 + 0*1) / 2 = 10
        //   median([20, 10]) = 10 + (20-10)/2 = 15
        // `is_some()` пропустил бы и «пронести последнее известное значение
        // вперёд», и «выбросить укороченный замер», и любую другую
        // раздутую/заниженную бухгалтерию по недостающему уровню — только
        // точное число различает их.
        assert_eq!(time_weighted_median_depth_usd_e9(&samples, 2), Some(e9(15)));
    }

    // -- select_final_two -----------------------------------------------------

    fn measured(
        symbol: &str,
        depth: i64,
        turnover: i64,
        events: i64,
        window_secs: i64,
    ) -> MeasuredCandidate {
        MeasuredCandidate {
            symbol: symbol.to_string(),
            window_start_utc_ms: 0,
            window_secs,
            events,
            median_depth_usd_e9: depth,
            reported_turnover_usd_e9: turnover,
        }
    }

    #[test]
    fn select_final_two_excludes_candidates_below_the_depth_floor() {
        let m = vec![
            measured("HI", DEPTH_FLOOR_USD_E9 + 1, 0, 10, 3600),
            measured("LO", DEPTH_FLOOR_USD_E9 - 1, 0, 10, 3600),
        ];
        let out = select_final_two(&m).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].symbol, "HI");
    }

    #[test]
    fn select_final_two_errors_when_all_candidates_are_below_the_floor() {
        let m = vec![measured("A", DEPTH_FLOOR_USD_E9 - 1, 0, 1, 3600)];
        assert_eq!(
            select_final_two(&m).unwrap_err(),
            PickError::NoSurvivorsAboveDepthFloor {
                floor_usd_e9: DEPTH_FLOOR_USD_E9,
                candidates: 1
            }
        );
    }

    #[test]
    fn select_final_two_on_empty_input_is_an_error_not_a_panic() {
        assert!(select_final_two(&[]).is_err());
    }

    /// Требуемый тест: отчётный оборот не может продвинуть кандидата ниже
    /// порога глубины — победитель определяется исключительно измеренной
    /// глубиной среди тех, кто прошёл порог.
    #[test]
    fn reported_turnover_cannot_promote_a_candidate_below_the_depth_floor() {
        let m = vec![
            measured(
                "HUGE_TURNOVER_THIN_BOOK",
                DEPTH_FLOOR_USD_E9 - 1,
                e9(999_999_999),
                1000,
                3600,
            ),
            measured(
                "SMALL_TURNOVER_DEEP_BOOK",
                DEPTH_FLOOR_USD_E9 + 1,
                e9(1),
                1,
                3600,
            ),
        ];
        let out = select_final_two(&m).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].symbol, "SMALL_TURNOVER_DEEP_BOOK");
    }

    #[test]
    fn select_final_two_ranks_survivors_by_measured_depth_descending() {
        let m = vec![
            measured("LOW", DEPTH_FLOOR_USD_E9 + 100, 0, 1, 3600),
            measured("HIGH", DEPTH_FLOOR_USD_E9 + 999, 0, 1, 3600),
            measured("MID", DEPTH_FLOOR_USD_E9 + 500, 0, 1, 3600),
        ];
        let out = select_final_two(&m).unwrap();
        assert_eq!(
            out.iter().map(|c| c.symbol.clone()).collect::<Vec<_>>(),
            vec!["HIGH", "MID"]
        );
    }

    /// Требуемый тест: равная измеренная глубина — победитель по темпу
    /// событий, а не по порядку появления во входном срезе.
    #[test]
    fn ties_in_depth_break_by_event_rate_deterministically() {
        let depth = DEPTH_FLOOR_USD_E9 + 1;
        let slow = measured("SLOW", depth, 0, 10, 3600); // 10 событий/час
        let fast = measured("FAST", depth, 0, 100, 3600); // 100 событий/час
        let forward = select_final_two(&[slow.clone(), fast.clone()]).unwrap();
        let backward = select_final_two(&[fast, slow]).unwrap();
        assert_eq!(forward[0].symbol, "FAST");
        assert_eq!(
            forward.iter().map(|c| c.symbol.clone()).collect::<Vec<_>>(),
            backward
                .iter()
                .map(|c| c.symbol.clone())
                .collect::<Vec<_>>(),
            "порядок входа не должен влиять на результат"
        );
    }

    #[test]
    fn ties_in_depth_and_event_rate_break_by_symbol() {
        let depth = DEPTH_FLOOR_USD_E9 + 1;
        let m = vec![
            measured("Z", depth, 0, 10, 3600),
            measured("A", depth, 0, 10, 3600),
        ];
        let out = select_final_two(&m).unwrap();
        assert_eq!(out[0].symbol, "A");
    }

    #[test]
    fn event_rate_comparison_does_not_overflow_on_extreme_counters() {
        let depth = DEPTH_FLOOR_USD_E9 + 1;
        let extreme = measured("EXTREME", depth, 0, i64::MAX, 1);
        let normal = measured("NORMAL", depth, 0, 1, 1);
        let out = select_final_two(&[extreme, normal]).unwrap();
        assert_eq!(
            out[0].symbol, "EXTREME",
            "не должно паниковать и обязано ранжировать верно"
        );
    }

    #[test]
    fn select_final_two_returns_one_when_only_one_candidate_survives() {
        let m = vec![measured("ONLY", DEPTH_FLOOR_USD_E9 + 1, 0, 1, 3600)];
        assert_eq!(select_final_two(&m).unwrap().len(), 1);
    }

    // -- min_lot_satisfies_min_notional (Decision 22) ------------------------

    #[test]
    fn min_lot_notional_passes_when_it_exceeds_the_requirement() {
        // 0.1 SOL (100_000_000 в масштабе 1e9) @ $150 = $15, требование $5.
        assert!(min_lot_satisfies_min_notional(100_000_000, e9(150), e9(5)));
    }

    #[test]
    fn min_lot_notional_fails_when_it_falls_short() {
        // 0.001 BTC @ $20 = $0.02, требование $5 — не хватает на два порядка.
        assert!(!min_lot_satisfies_min_notional(1_000_000, e9(20), e9(5)));
    }

    #[test]
    fn min_lot_notional_exactly_at_the_boundary_passes() {
        // Ровно требование — нестрогое ">=", а не строгое "> ".
        assert!(min_lot_satisfies_min_notional(e9(1), e9(1), e9(1)));
    }

    #[test]
    fn min_lot_notional_zero_requirement_is_trivially_satisfied() {
        assert!(min_lot_satisfies_min_notional(1, 1, 0));
    }

    #[test]
    fn min_lot_notional_does_not_overflow_on_large_realistic_values() {
        // Дорогой актив с крупным минимальным лотом — всё ещё далеко от
        // переполнения i128 (см. doc функции: произведение уходит в 1e18,
        // а i128 держит на много порядков больше).
        assert!(min_lot_satisfies_min_notional(
            e9(1_000),
            e9(1_000_000),
            e9(5)
        ));
    }

    // -- format_e9 -------------------------------------------------------

    #[test]
    fn format_e9_round_trips_through_parse_e9() {
        use crate::bybit::ws::parse_e9;
        for &s in &["150.01", "0.001", "12345.6789", "1", "0.000000001", "0"] {
            let v = parse_e9(s).unwrap();
            assert_eq!(parse_e9(&format_e9(v)).unwrap(), v);
        }
    }

    // -- wall_clock_ns/wall_clock_ms — не переизобретают исправленную панику --

    /// Регрессия: здесь раньше стоял `SystemTime::now().duration_since(
    /// UNIX_EPOCH).expect(...)` — ровно тот дефект, который
    /// `bybit::conn::SystemClock::ns_since_epoch` уже один раз исправил (см.
    /// его doc: мёртвая RTC или несконфигурированная VM отдают время раньше
    /// эпохи, и `.expect()` на этом падает на первом же вызове). Грепает
    /// исходник этого же файла — тот же приём, что `ARCHITECTURE.md` уже
    /// использует для границы `signal/`/`strategy/` («это проверяется
    /// тестом, который грепает дерево модулей, а не дисциплиной»): свойство
    /// «здесь нет .expect() на системных часах» не выразить иначе без
    /// инъекции часов, которой у `wall_clock_ns` по конструкции нет.
    #[test]
    fn wall_clock_does_not_reintroduce_the_fixed_unix_epoch_panic() {
        let source = include_str!("lob.rs");
        assert!(
            !source.contains(".expect(\"системные часы обязаны быть после эпохи Unix\")"),
            "wall_clock_ns/wall_clock_ms обязаны брать время через \
             bybit::conn::SystemClock::now_ns() (не паникующий), а не через \
             собственный SystemTime::now().duration_since(UNIX_EPOCH).expect(...)"
        );
    }

    // -- write_candidate_table_csv / write_instruments_csv (CSV round-trip) --

    /// Зеркало `CandidateRow` для чтения назад: `tercile: &'static str` не
    /// десериализуется (нет `Deserialize` для заимствованной строки с
    /// произвольным временем жизни из CSV-буфера), поэтому у поля здесь —
    /// `String`. Остальные поля и их порядок — те же, чтобы сравнение было
    /// прямым свидетельством того, что `write_candidate_table_csv` пишет
    /// ровно то, что было передано.
    #[derive(Debug, PartialEq, serde::Deserialize)]
    struct CandidateRowOwned {
        symbol: String,
        turnover_24h_usd_e9: i64,
        tercile: String,
        prefiltered: bool,
        measured: bool,
        window_start_utc_ms: Option<i64>,
        window_secs: Option<i64>,
        events: Option<i64>,
        median_depth_usd_e9: Option<i64>,
        above_depth_floor: Option<bool>,
        selected_for_pilot: bool,
        final_rank: Option<u8>,
    }

    /// Требуемый тест: `write_candidate_table_csv` — единственное место, где
    /// строится файл, названный в done-condition шага 0.4 («таблица
    /// кандидатов ... закоммичена»), и до этого теста ни один тест файла не
    /// доходил до настоящего `csv::Writer`. Одна строка целиком `Some`
    /// (измеренный, отобранный кандидат), одна — целиком `None`
    /// (неизмеренный: предфильтр его отсеял до сети) — девять
    /// `Option`-полей `CandidateRow`, и `None`-ветка — та, что молча ломается
    /// первой, если формат столбцов когда-нибудь разойдётся со структурой.
    #[test]
    fn candidate_table_csv_round_trips_measured_and_unmeasured_rows() {
        let rows = vec![
            CandidateRow {
                symbol: "MID0USDT".to_string(),
                turnover_24h_usd_e9: e9(1_000_000),
                tercile: "middle",
                prefiltered: true,
                measured: true,
                window_start_utc_ms: Some(1_700_000_000_000),
                window_secs: Some(3600),
                events: Some(12_345),
                median_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(1)),
                above_depth_floor: Some(true),
                selected_for_pilot: true,
                final_rank: Some(1),
            },
            CandidateRow {
                symbol: "TAIL0USDT".to_string(),
                turnover_24h_usd_e9: e9(100),
                tercile: "bottom",
                prefiltered: false,
                measured: false,
                window_start_utc_ms: None,
                window_secs: None,
                events: None,
                median_depth_usd_e9: None,
                above_depth_floor: None,
                selected_for_pilot: false,
                final_rank: None,
            },
        ];

        let tmp = tempfile::NamedTempFile::new().unwrap();
        write_candidate_table_csv(tmp.path(), &rows).unwrap();

        let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
        let read_back: Vec<CandidateRowOwned> =
            reader.deserialize().collect::<Result<_, _>>().unwrap();

        assert_eq!(
            read_back,
            vec![
                CandidateRowOwned {
                    symbol: "MID0USDT".to_string(),
                    turnover_24h_usd_e9: e9(1_000_000),
                    tercile: "middle".to_string(),
                    prefiltered: true,
                    measured: true,
                    window_start_utc_ms: Some(1_700_000_000_000),
                    window_secs: Some(3600),
                    events: Some(12_345),
                    median_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(1)),
                    above_depth_floor: Some(true),
                    selected_for_pilot: true,
                    final_rank: Some(1),
                },
                CandidateRowOwned {
                    symbol: "TAIL0USDT".to_string(),
                    turnover_24h_usd_e9: e9(100),
                    tercile: "bottom".to_string(),
                    prefiltered: false,
                    measured: false,
                    window_start_utc_ms: None,
                    window_secs: None,
                    events: None,
                    median_depth_usd_e9: None,
                    above_depth_floor: None,
                    selected_for_pilot: false,
                    final_rank: None,
                },
            ],
            "неизмеренный кандидат обязан вернуться как None на всех девяти \
             Option-полях, а не как 0, false или пустая строка, принятая за None"
        );
    }

    /// Требуемый тест: `write_instruments_csv` — то, что done-condition шага
    /// 0.4 называет «`instruments.csv` непуст». Круглый путь через
    /// `format_e9` (не `parse_e9`, отдельная копия — см. её doc) проверяется
    /// здесь на реальном файле, а не только опосредованно через
    /// `format_e9_round_trips_through_parse_e9`.
    #[test]
    fn instruments_csv_round_trips_and_is_nonempty() {
        let instruments = vec![Instrument {
            symbol: "SOLUSDT".to_string(),
            base_coin: "SOL".to_string(),
            quote_coin: "USDT".to_string(),
            contract_type: "LinearPerpetual".to_string(),
            status: "Trading".to_string(),
            launch_time_ms: Some(1_600_000_000_000),
            tick_e9: 10_000_000,
            min_order_qty_e9: 100_000_000,
            qty_step_e9: 100_000_000,
            min_notional_value_e9: 5_000_000_000,
        }];

        let tmp = tempfile::NamedTempFile::new().unwrap();
        write_instruments_csv(tmp.path(), &instruments).unwrap();

        let content = std::fs::read_to_string(tmp.path()).unwrap();
        assert!(
            !content.trim().is_empty(),
            "instruments.csv обязан быть непуст (done-condition шага 0.4)"
        );

        let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
        let read_back: Vec<InstrumentRow> = reader.deserialize().collect::<Result<_, _>>().unwrap();
        assert_eq!(
            read_back,
            vec![InstrumentRow {
                symbol: "SOLUSDT".to_string(),
                tick_size: "0.01".to_string(),
                min_order_qty: "0.1".to_string(),
                qty_step: "0.1".to_string(),
                min_notional_value: "5".to_string(),
            }]
        );
    }

    // -- book_level_notionals_usd_e9 — одна аллокация на обе стороны --------

    /// Требуемый тест: раньше это были два вызова (по одному на сторону) плюс
    /// `Vec::extend`, что почти всегда даёт три аллокации на обновление книги
    /// (см. doc функции). `alloc_count::measure` — тот же счётчик и тот же
    /// приём, что уже используют `stats/mod.rs::
    /// the_replication_loop_does_not_allocate_per_replicate`,
    /// `bybit::clock.rs` и `binlog/mod.rs`: он бы не заметил регресс с одной
    /// аллокации до трёх, если бы измерялось только количество элементов.
    #[test]
    fn book_level_notionals_usd_e9_collects_both_sides_in_one_allocation() {
        use crate::book::{Book, Update};

        let mut book = Book::new(1, 1);
        book.apply(&Update {
            is_snapshot: true,
            u: 1,
            cts_ms: 0,
            bids: vec![(100, 5), (99, 3), (98, 1)],
            asks: vec![(101, 2), (102, 4)],
        })
        .unwrap();

        let (levels, counts) =
            crate::alloc_count::measure(|| book_level_notionals_usd_e9(&book, 1, 1));

        assert_eq!(levels.len(), 5, "три бида плюс два аска");
        assert_eq!(
            counts.allocations, 1,
            "обе стороны обязаны попасть в один вектор одной аллокацией \
             через Iterator::chain + collect, не через Vec::extend"
        );
    }

    // -- measure_one_symbol — общий дедлайн, не Instant::now() внутри задачи -

    /// Требуемый тест: без сети, но не тавтологический — гоняет настоящую
    /// `measure_one_symbol` с настоящим `BybitPublicLinearConnector` (сеть
    /// нужна только внутри отдельно заспавненной задачи `conn.run(...)`,
    /// которую эта функция никогда не ждёт до своего собственного дедлайна).
    /// Симулирует ровно сценарий finding'а: задача добралась до цикла позже,
    /// чем был посчитан дедлайн (здесь — позже самого дедлайна). Старая
    /// реализация принимала `duration: Duration` и считала `Instant::now() +
    /// duration` заново при каждом вызове — с ней это же обращение
    /// проработало бы ещё почти полный `duration`, а не вернулось сразу.
    #[tokio::test]
    async fn measure_one_symbol_stops_at_the_shared_deadline_even_if_started_late() {
        let deadline = tokio::time::Instant::now() + Duration::from_millis(40);
        // «Поздний старт»: тем временем, что делит все символы одного
        // замера, уже распорядились — деконнект, бэкофф, планировщик.
        tokio::time::sleep(Duration::from_millis(80)).await;

        let began = tokio::time::Instant::now();
        let samples = measure_one_symbol("SOLUSDT".to_string(), 1, 1, deadline).await;
        let elapsed = began.elapsed();

        assert!(
            samples.is_empty(),
            "дедлайн уже в прошлом на момент вызова — цикл не должен успеть ни одного замера"
        );
        assert!(
            elapsed < Duration::from_millis(30),
            "функция обязана вернуться немедленно, если переданный дедлайн уже \
             в прошлом, а не отсчитывать duration заново от своего собственного \
             старта — заняло {elapsed:?}"
        );
    }

    // -- пайплайн целиком, синтетическая вселенная ---------------------------

    /// Собирает весь путь Decision 18 от сырых метаданных до финальных двух
    /// на небольшой, но не тривиальной синтетической вселенной — то самое
    /// «структурировано так, что правило — чистая функция», проверенное
    /// сквозным прогоном, а не только по стадиям порознь.
    #[test]
    fn full_pipeline_from_synthetic_universe_matches_hand_computed_result() {
        // Три группы по шесть — ровные трети (см. doc `tercile_bucket`), так
        // что группы совпадают с бакетами без остатка на границе.
        let mut candidates = vec![
            meta("BTCUSDT", "BTC", e9(20_000_000)),
            meta("ETHUSDT", "ETH", e9(19_000_000)),
        ];
        for i in 2..6 {
            candidates.push(meta(
                &format!("BIG{i}USDT"),
                &format!("BIG{i}"),
                e9(18_000_000 - i * 1_000_000),
            ));
        }
        for i in 0..6 {
            candidates.push(meta(
                &format!("MID{i}USDT"),
                &format!("MID{i}"),
                e9(1_000_000 - i * 10_000),
            ));
        }
        for i in 0..6 {
            candidates.push(meta(
                &format!("TAIL{i}USDT"),
                &format!("TAIL{i}"),
                e9(100 - i),
            ));
        }
        // Токенизированная акция с высоким оборотом — обязана выпасть уже на
        // стадии пула, до всякого терциля.
        let mut stock = meta("AAPLUSDT", "AAPL", e9(5_000_000));
        stock.is_tokenized_equity = true;
        candidates.push(stock);

        let pool = build_pool(&candidates, NOW_MS);
        assert_eq!(pool.len(), 18, "AAPL исключена, остальные 18 — в пуле");

        let tercile = middle_tercile(&pool).unwrap();
        assert_eq!(
            tercile.len(),
            6,
            "средняя треть восемнадцати — шесть записей"
        );
        assert!(tercile.iter().all(|c| c.symbol.starts_with("MID")));

        let prefiltered = prefilter_top_n(&tercile, PREFILTER_TOP_N);
        assert_eq!(
            prefiltered.len(),
            6,
            "меньше 20 — предфильтр не режет ничего"
        );

        // Синтетический замер: MID0 — глубокий и активный, MID1 — глубокий,
        // но реже торгуется, остальные — ниже порога.
        let measured: Vec<MeasuredCandidate> = prefiltered
            .iter()
            .map(|c| {
                let depth = if c.symbol == "MID0USDT" || c.symbol == "MID1USDT" {
                    DEPTH_FLOOR_USD_E9 + e9(1)
                } else {
                    DEPTH_FLOOR_USD_E9 - e9(1)
                };
                let events = if c.symbol == "MID0USDT" { 500 } else { 100 };
                measured(&c.symbol, depth, c.turnover_24h_usd_e9, events, 3600)
            })
            .collect();

        let selected = select_final_two(&measured).unwrap();
        assert_eq!(
            selected
                .iter()
                .map(|c| c.symbol.clone())
                .collect::<Vec<_>>(),
            vec!["MID0USDT", "MID1USDT"]
        );

        let table = build_candidate_table(&pool, &prefiltered, &measured, &selected);
        assert_eq!(
            table.len(),
            18,
            "таблица несёт весь пул, не только выживших"
        );
        let btc_row = table.iter().find(|r| r.symbol == "BTCUSDT").unwrap();
        assert_eq!(btc_row.tercile, "top");
        assert!(!btc_row.selected_for_pilot);
        let mid0_row = table.iter().find(|r| r.symbol == "MID0USDT").unwrap();
        assert_eq!(mid0_row.tercile, "middle");
        assert!(mid0_row.prefiltered);
        assert!(mid0_row.selected_for_pilot);
        assert_eq!(mid0_row.final_rank, Some(1));
    }
}
