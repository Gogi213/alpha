//! `lob <подкоманда>` — единственная точка входа для всех чисел отчёта.
//!
//! Десять подкоманд (шаг 0.9, Goal: каждое число — одной командой):
//! `pick` (0.4), `record` (0.3), `verify` (0.6), `export` (6.1) —
//! существующие; `clock` (0.5), `probe` (6.2), `levels` (1.1, 1.2),
//! `markout` (2.1), `watch` (4.1), `pilot` (3.1) — тонкие обёртки
//! поверх уже протестированной логики своих модулей: здесь только
//! CLI-аргументы, печать артефакта и коды выхода, бизнес-логики нет.
//! Чистая логика записи живёт в `super::record`, здесь — только вариант
//! команды и печать итога.
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

use crate::binlog::{Reader, Record};
use crate::book::{Book, Side};
use crate::bybit::clock::{
    check_rows, run as run_clock_loop, BybitServerTimeSource, ClockRow, ReferenceClock, RoundTrip,
    UdpNtpSource,
};
use crate::bybit::conn::SystemClock;
use crate::bybit::probe::{
    run_cycles, summarize, BybitPrivateRest, OrderSide, PrivateRest, ProbeError, ProbeParams,
    RttSummary, SignedRequest, DEFAULT_RECV_WINDOW_MS, MIN_CYCLES,
};
use crate::bybit::rest::{
    fetch_all_linear_instruments, fetch_linear_tickers, BybitPublicRest, Instrument,
    BYBIT_MAINNET_URL,
};
use crate::bybit::sign::Credentials;
use crate::bybit::verify::FileReplayer;
use crate::bybit::verify_sidecar::{read_verify_rows, verify_csv_path, VerifyVerdict};
use crate::commands::record::{gaps_csv_path, read_gap_rows, GapKind};
use crate::lob::costs::{
    mean_net_bps, Observation, MAKER_FEE_BPS, ROUNDTRIP_FEES_BPS, TAKER_FEE_BPS,
};
use crate::lob::levels::{
    DeathKind, LevelObs, LevelRecord, LevelTracker, LevelsConfig, Outcome, TradeHit,
};
use crate::lob::markout::{base_before, markouts_for_level, MidSample, HORIZONS_MS};
use crate::lob::markup::{run_confirmatory, ConfirmatoryDay};
use crate::lob::runs::log_pilot_run;
use crate::lob::watch::{
    is_c1, progress_csv_path, ready_flag_path, require_ready_flag, tally_day, DayTally, WatchState,
};
use hftbacktest::types::{LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT};

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
/// Decision 18(б), ревизия 10: порог проверяется на бид и на аск **раздельно**
/// — см. `select_final_two` и doc `DepthSample`.
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
    /// Ни один измеренный кандидат не прошёл порог глубины **на обеих
    /// сторонах** (Decision 18(б), ревизия 10) — толстая сторона не
    /// засчитывается за тонкую.
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
                "ни один из {candidates} измеренных кандидатов не набрал {} USD медианной глубины на уровень на обеих сторонах",
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

/// Строит пул Decision 18: linear USDT-перпы, торгуются >= 30 суток,
/// дубликаты одного актива схлопнуты в один — тот, у которого выше оборот
/// (актуальный контракт этого актива), при равенстве оборота —
/// лексикографически меньший символ (детерминизм, не смысл).
///
/// **Здесь нет фильтра токенизированных акций, и это не пробел.** Ревизии
/// 4-9 называли их в исключениях отдельным пунктом, что толкало реализацию
/// к списку тикеров — списку, который колонка «Отвергнуто» этого же решения
/// прямо запрещает (он устаревает при каждой смене листингов, ровно как
/// список, от которого Decision 18 уходит для терциля). Ревизия 10 убрала
/// пункт целиком: у Bybit токенизированные акции — не бессрочные контракты
/// категории `linear`, поэтому `instruments-info?category=linear`, который
/// наполняет `candidates` этой функции, их и не возвращает — фильтровать
/// здесь уже нечего. Если этот комментарий читается после регрессии (тикер
/// акции всё же попал в выдачу `lob pick`) — чинить нужно категорию
/// REST-запроса (`fetch_all_linear_instruments`/`rest::CATEGORY_LINEAR`),
/// а не добавлять сюда список исключений.
pub fn build_pool(candidates: &[CandidateMeta], now_ms: i64) -> Vec<PoolCandidate> {
    let mut best: BTreeMap<&str, &CandidateMeta> = BTreeMap::new();
    for c in candidates {
        if c.quote_coin != LINEAR_QUOTE_COIN {
            continue;
        }
        if c.contract_type != LINEAR_CONTRACT_TYPE {
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
/// `local_ts_ns`, что `bybit::conn` ставит до разбора, H12) и номинал
/// каждого уровня топ-50 в USD·1e9 на этот момент, **раздельно по стороне**.
///
/// Decision 18(б), ревизия 10, закрыла двусмысленность, которую первая
/// реализация читала иначе: «топ-50» — это пятьдесят уровней **одной**
/// стороны, а не сто пополам, и порог глубины обязан выполняться на бид и
/// на аск **независимо**. Причина — операционная, не эстетическая: отдыхать
/// ордером можно только на одной стороне книги, и объединённая медиана по
/// сотне уровней прячет систематически тонкую сторону за толстой ровно там,
/// где заявка и упёрлась бы в исполнение. Раньше здесь было одно поле
/// `level_notional_usd_e9`, собранное `Iterator::chain` из обеих сторон, —
/// то самое прочтение, которое ревизия 10 отвергла явно.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DepthSample {
    pub at_ns: i64,
    pub bid_notional_usd_e9: Vec<i64>,
    pub ask_notional_usd_e9: Vec<i64>,
}

/// Время-взвешенная медиана глубины **одной стороны** за окно
/// `[samples[0].at_ns, window_end_ns)` (Decision 18: «глубина считается
/// времени-взвешенно», Decision 18(б): раздельно по стороне).
///
/// **Порядок операций — Decision 18(в), ревизия 10, задаёт его явно и в этом
/// порядке:** номинал уровня (размер × цена) в каждом снимке — уже готов на
/// входе, `levels_of(s)` читает его из `DepthSample`, посчитанного раньше;
/// затем медиана **по уровням этого снимка** — `median_depth_per_level_usd_e9`
/// ниже, применённая к каждому снимку отдельно, даёт один скаляр на снимок;
/// и только затем — взвешивание этих скаляров по времени между снимками
/// (длительностью до следующего снимка, для последнего — до конца окна).
///
/// Более ранняя реализация (до ревизии 10, и её собственный doc-комментарий
/// здесь же аргументировал за неё на нескольких абзацах) делала обратное:
/// сперва взвешенное по времени среднее для каждой **позиции** уровня за
/// весь час, и лишь затем медиана по ~50 усреднённым числам. План не считал
/// этот порядок согласованным нигде до ревизии 10 — предыдущий комментарий
/// заявлял обратное, ссылаясь на скобку самого Decision 18 как на решённый
/// вопрос порядка, и это было ошибкой чтения, а не решением плана: скобка
/// говорит про медиану по уровням против суммы по ним (пункт (а)), а не про
/// то, в каком порядке эта медиана встречается со временем. Ревизия 10
/// впервые называет порядок словами и явно отвергает обратный: «номинал от
/// медианного размера считает деньги по цене, которой на тонкой стороне
/// может не быть» — здесь этот обратный порядок и недостижим по построению,
/// потому что на вход уже приходит номинал (размер, уже умноженный на цену
/// того же снимка), а не размер отдельно от цены.
///
/// Побочный эффект правильного порядка: снимкам разной длины (глубина книги
/// у края топ-50 дрожит) больше не нужно взаимное выравнивание позиций —
/// медиана каждого снимка берётся по его собственным уровням, и короткий
/// снимок не голосует «нулевым» уровнем за позицию, которой на бирже не
/// было. Снимок вовсе без уровней этой стороны (`median_depth_per_level_usd_e9`
/// возвращает `None` — см. её doc: пустой вход значит «данных нет», а не
/// «глубина ноль») пропускается целиком и не взвешивается: секундный пробел
/// на одной стороне внутри часа живого потока не должен обнулять весь замер.
fn time_weighted_median_for_side(
    samples: &[DepthSample],
    window_end_ns: i64,
    levels_of: impl Fn(&DepthSample) -> &[i64],
) -> Option<i64> {
    let mut weighted_sum: i128 = 0;
    let mut total_weight_ns: i128 = 0;
    for (i, s) in samples.iter().enumerate() {
        let Some(snapshot_median) = median_depth_per_level_usd_e9(levels_of(s)) else {
            continue;
        };
        let next_at = samples.get(i + 1).map_or(window_end_ns, |next| next.at_ns);
        let dt = (next_at - s.at_ns).max(0) as i128;
        total_weight_ns += dt;
        weighted_sum += snapshot_median as i128 * dt;
    }
    if total_weight_ns == 0 {
        return None;
    }
    Some((weighted_sum / total_weight_ns) as i64)
}

/// Время-взвешенная медианная глубина стороны **бид** — см.
/// `time_weighted_median_for_side` про порядок операций.
pub fn time_weighted_median_bid_depth_usd_e9(
    samples: &[DepthSample],
    window_end_ns: i64,
) -> Option<i64> {
    time_weighted_median_for_side(samples, window_end_ns, |s| s.bid_notional_usd_e9.as_slice())
}

/// Время-взвешенная медианная глубина стороны **аск** — см.
/// `time_weighted_median_for_side` про порядок операций.
pub fn time_weighted_median_ask_depth_usd_e9(
    samples: &[DepthSample],
    window_end_ns: i64,
) -> Option<i64> {
    time_weighted_median_for_side(samples, window_end_ns, |s| s.ask_notional_usd_e9.as_slice())
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
    /// Число событий книги за окно — тот же счётчик, что определяет оба
    /// поля глубины ниже через `time_weighted_median_{bid,ask}_depth_usd_e9`
    /// (там это `samples.len()`): каждый принятый апдейт книги — одно
    /// событие и один замер разом, отдельного счётчика заводить незачем.
    pub events: i64,
    /// Decision 18(б), ревизия 10: «топ-50» — пятьдесят уровней одной
    /// стороны, порог и ранжирование читают обе стороны раздельно, не одно
    /// объединённое число (см. doc `DepthSample`).
    pub median_bid_depth_usd_e9: i64,
    pub median_ask_depth_usd_e9: i64,
    /// Только для печати в таблице (Decision 18: «отчётный оборот никогда не
    /// критерий») — `select_final_two` этого поля не читает вовсе.
    pub reported_turnover_usd_e9: i64,
}

impl MeasuredCandidate {
    /// Худшая из двух сторон — связывающая величина и для порога, и для
    /// ранжирования: отдыхать ордером можно только на одной стороне, и
    /// именно она определяет, исполнится ли заявка, а не более толстая
    /// соседняя (см. doc `DepthSample`, Decision 18(б)).
    pub fn min_side_depth_usd_e9(&self) -> i64 {
        self.median_bid_depth_usd_e9
            .min(self.median_ask_depth_usd_e9)
    }
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

/// Финальная стадия Decision 18: порог глубины обязан пройти на **обеих**
/// сторонах независимо (18(б)) — не сумма и не среднее двух; ранг по
/// худшей из двух сторон (`min_side_depth_usd_e9`, та же логика, что и
/// сам порог: толстая сторона не должна прятать тонкую ни в гейте, ни в
/// ранжировании), при равенстве — по темпу событий, при равенстве и там —
/// по символу (детерминизм, не смысл). Отчётный оборот здесь не участвует
/// вовсе, поэтому не может продвинуть кандидата ниже порога.
pub fn select_final_two(
    measured: &[MeasuredCandidate],
) -> Result<Vec<MeasuredCandidate>, PickError> {
    let mut survivors: Vec<&MeasuredCandidate> = measured
        .iter()
        .filter(|m| {
            m.median_bid_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
                && m.median_ask_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
        })
        .collect();
    if survivors.is_empty() {
        return Err(PickError::NoSurvivorsAboveDepthFloor {
            floor_usd_e9: DEPTH_FLOOR_USD_E9,
            candidates: measured.len(),
        });
    }
    survivors.sort_by(|a, b| {
        b.min_side_depth_usd_e9()
            .cmp(&a.min_side_depth_usd_e9())
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
    /// Decision 18(б), ревизия 10: колонки раздельно по стороне — одно
    /// объединённое число пряталось бы за толстой стороной.
    pub median_bid_depth_usd_e9: Option<i64>,
    pub median_ask_depth_usd_e9: Option<i64>,
    /// `true`, только если порог пройден на **обеих** сторонах.
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
                median_bid_depth_usd_e9: m.map(|m| m.median_bid_depth_usd_e9),
                median_ask_depth_usd_e9: m.map(|m| m.median_ask_depth_usd_e9),
                above_depth_floor: m.map(|m| {
                    m.median_bid_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
                        && m.median_ask_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
                }),
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

/// Номинал каждого уровня **одной стороны** книги в USD·1e9, одним вектором
/// и одной аллокацией: `Book::levels` отдаёт `Map<Range<usize>, _>`, чей
/// `size_hint` точен, и `collect` резервирует нужный размер заранее.
///
/// Раньше эта функция принимала обе стороны разом (`Iterator::chain` бид с
/// аском в один вектор) — Decision 18(б), ревизия 10, запрещает объединять
/// стороны: порог глубины обязан проверяться на каждой независимо (см. doc
/// `DepthSample`), и общий вектор для этого уже не годится. По одному вызову
/// на сторону на каждое принятое обновление книги — на часовом замере с
/// `PREFILTER_TOP_N` параллельными символами это не косметика: событие
/// книги — самый частый код этого модуля.
fn book_level_notionals_usd_e9(
    book: &crate::book::Book,
    side: crate::book::Side,
    tick_e9: i64,
    step_e9: i64,
) -> Vec<i64> {
    book.levels(side)
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
/// последний замер каждого символа (`time_weighted_median_bid_depth_usd_e9`,
/// `time_weighted_median_ask_depth_usd_e9`).
async fn measure_one_symbol(
    symbol: String,
    tick_e9: i64,
    step_e9: i64,
    deadline: tokio::time::Instant,
) -> Vec<DepthSample> {
    use crate::book::{Book, Side};
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
                        bid_notional_usd_e9: book_level_notionals_usd_e9(
                            &book,
                            Side::Bid,
                            tick_e9,
                            step_e9,
                        ),
                        ask_notional_usd_e9: book_level_notionals_usd_e9(
                            &book,
                            Side::Ask,
                            tick_e9,
                            step_e9,
                        ),
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
        // Обе стороны обязаны иметь измерение (Decision 18(б)) — кандидат
        // без данных на какой-либо из сторон пропускается целиком, так же
        // как раньше пропускался кандидат без единого объединённого числа.
        let Some(median_bid_depth_usd_e9) =
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns)
        else {
            continue;
        };
        let Some(median_ask_depth_usd_e9) =
            time_weighted_median_ask_depth_usd_e9(&samples, window_end_ns)
        else {
            continue;
        };
        out.push(MeasuredCandidate {
            symbol,
            window_start_utc_ms,
            window_secs: window_secs as i64,
            events: samples.len() as i64,
            median_bid_depth_usd_e9,
            median_ask_depth_usd_e9,
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
    /// Непрерывная запись потока в суточные файлы (шаг 0.3, Decision 7/23).
    Record(super::record::RecordArgs),
    /// Сверка записанных суток: инварианты и сделки в диапазоне книги (шаг 0.6).
    /// Сверка с REST по u — только живой поток (у файла нет u), см. verify.rs.
    Verify(crate::bybit::verify::VerifyArgs),
    /// Экспорт суток в `npy` для крейта `hftbacktest` (шаг 6.1, Decision 17).
    Export(crate::lob::export::ExportArgs),
    /// Замер смещения часов хоста против NTP и `serverTime` (шаг 0.5).
    Clock(ClockArgs),
    /// Распределение RTT полного цикла post-only ордера (шаг 6.2).
    Probe(ProbeArgs),
    /// Разметка уровней с шестью признаками истории и классом (шаги 1.1, 1.2).
    Levels(LevelsArgs),
    /// Markout уровней на четырёх горизонтах (шаг 2.1).
    Markout(MarkoutArgs),
    /// Счётчик n/G для C2, `progress.csv` и `ready.flag` (шаг 4.1).
    Watch(WatchArgs),
    /// Пилотная цепочка 1.1 → 1.2 → 2.1 тем же кодом и вердикт G0 (шаг 3.1).
    Pilot(PilotArgs),
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
                    "pilot: {} (бид {} / аск {} USD·1e-9)",
                    m.symbol, m.median_bid_depth_usd_e9, m.median_ask_depth_usd_e9
                );
            }
            Ok(())
        }
        LobCommand::Record(args) => {
            let summary = super::record::run_record(&args)?;
            println!(
                "record: {} records={} files={} gaps={} stop={:?}",
                summary.symbol,
                summary.records_total,
                summary.files.len(),
                summary.gaps.len(),
                summary.stop_reason
            );
            Ok(())
        }
        LobCommand::Verify(args) => {
            let summary = crate::bybit::verify::run_verify(&args)?;
            println!(
                "verify: files={} updates={} gaps={} invariants={} trades={} out_of_range={} indeterminate={}",
                summary.files,
                summary.updates_applied,
                summary.sequence_gaps,
                summary.invariant_violations,
                summary.trades_total,
                summary.trades_out_of_range,
                summary.trades_indeterminate,
            );
            Ok(())
        }
        LobCommand::Export(args) => {
            let summary = crate::lob::export::run_export(&args)?;
            println!(
                "export: files={} events={} defective={} out={}",
                summary.files,
                summary.events,
                summary.defective,
                summary.out.display()
            );
            Ok(())
        }
        LobCommand::Clock(args) => {
            let rows = run_clock(&args)?;
            let violations = check_rows(&rows);
            println!(
                "clock: rows={} violations={} out={}",
                rows.len(),
                violations.len(),
                args.root.join("clock.csv").display()
            );
            for v in &violations {
                println!("clock violation: {v:?}");
            }
            Ok(())
        }
        LobCommand::Probe(args) => {
            let (summary, out) = run_probe(&args)?;
            println!(
                "probe: n={} median_ns={} p95_ns={} out={}",
                summary.n,
                summary.median_ns,
                summary.p95_ns,
                out.display()
            );
            Ok(())
        }
        LobCommand::Levels(args) => {
            let summary = run_levels(&args)?;
            println!(
                "levels: days={} levels={} out={}",
                summary.days,
                summary.levels,
                summary.out.display()
            );
            Ok(())
        }
        LobCommand::Markout(args) => {
            let summary = run_markout(&args)?;
            println!(
                "markout: days={} levels={} out={} {}",
                summary.days,
                summary.levels,
                summary.out.display(),
                summary.horizon_line,
            );
            Ok(())
        }
        LobCommand::Watch(args) => {
            let summary = run_watch(&args)?;
            println!(
                "watch: days={} n_c2={} g_c2={} flag={} out={}",
                summary.days,
                summary.n_c2,
                summary.g_c2,
                summary.flag.as_deref().unwrap_or("not-due"),
                summary.progress.display()
            );
            Ok(())
        }
        LobCommand::Pilot(args) => {
            let summary = run_pilot(&args)?;
            println!(
                "pilot: symbol={} n_pulled={} mean_m_10s_bps={:.3} mean_net_bps={:.3} {} runs={}",
                summary.symbol,
                summary.n_pulled,
                summary.mean_m_10s_bps,
                summary.mean_net_bps,
                summary.verdict,
                summary.runs_out.display()
            );
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
    let meta = join_candidate_meta(&instruments, &turnover_by_symbol);

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
        let out = join_candidate_meta(&instruments, &HashMap::new());
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

        let out = join_candidate_meta(&instruments, &turnover);

        assert_eq!(
            out.iter().map(|c| c.symbol.as_str()).collect::<Vec<_>>(),
            vec!["HASDATAUSDT"],
            "с тикером — остаётся, без тикера — выпадает, оба в одном вызове"
        );
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

    /// Требуемый тест (Decision 18, ревизия 10): отдельного правила против
    /// токенизированных акций больше нет — Bybit не возвращает их в пуле
    /// категории `linear` вовсе, поэтому фильтровать в коде уже нечего.
    /// Символ здесь проходит все ОСТАЛЬНЫЕ фильтры пула (USDT,
    /// LinearPerpetual, достаточный возраст) и обязан остаться: до ревизии
    /// 10 тот же символ можно было вычеркнуть флагом `is_tokenized_equity`
    /// (или CLI-флагом `--exclude-tokenized-equity`), которых больше нет ни
    /// в `CandidateMeta`, ни в `PickArgs`.
    ///
    /// **Почему одной проверки `build_pool(&[c], ..).len() == 1` тут
    /// недостаточно** (и почему первая версия этого теста не ловила
    /// регрессию, хотя выглядела так, будто должна). Старый механизм можно
    /// вернуть буквально: добавить в `CandidateMeta` поле
    /// `is_tokenized_equity: bool`, оставить ему значение по умолчанию
    /// `false` везде, где структура строится (в т.ч. в `meta()` ниже), и
    /// вернуть в `build_pool` `if c.is_tokenized_equity { continue }`. Раз
    /// значение по умолчанию — `false`, фильтр не сработает НИ НА ОДНОМ
    /// кандидате ни в одном тесте этого файла, и старая версия теста
    /// (только `build_pool` и длина результата) останется зелёной — список
    /// исключений, в котором ничего нет (или который никто не заполнил),
    /// неотличим по наблюдаемому поведению от полного отсутствия механизма.
    /// Значит, ловить регрессию поведением `build_pool` в принципе нельзя:
    /// нужно не пускать дело до фильтра вовсе.
    ///
    /// Поэтому ниже — исчерпывающая деструктуризация `CandidateMeta` без
    /// `..`: компилятор требует назвать здесь буквально каждое поле
    /// структуры. Если у неё когда-нибудь появится новое поле (не
    /// обязательно `is_tokenized_equity` — любое), эта строка перестанет
    /// собираться, пока кто-то не впишет его сюда осознанно и не объяснит,
    /// что с ним делать в `build_pool`. `cargo test` в этом случае падает
    /// на этапе компиляции раньше, чем успевает запуститься хоть один
    /// тест, — то есть красный результат гарантирован значением типа, а не
    /// удачным выбором тестовых данных.
    #[test]
    fn build_pool_includes_a_symbol_that_used_to_be_flagged_as_tokenized_equity() {
        let c = meta("TSLAUSDT", "TSLA", e9(1_000_000));

        let CandidateMeta {
            symbol,
            base_coin,
            quote_coin,
            contract_type,
            launch_time_ms,
            turnover_24h_usd_e9,
        } = c.clone();
        assert_eq!(symbol, "TSLAUSDT");
        assert_eq!(base_coin, "TSLA");
        assert_eq!(quote_coin, "USDT");
        assert_eq!(contract_type, "LinearPerpetual");
        assert_eq!(launch_time_ms, Some(0));
        assert_eq!(turnover_24h_usd_e9, e9(1_000_000));

        let pool = build_pool(&[c], NOW_MS);
        assert_eq!(
            pool.len(),
            1,
            "символа, похожего на токенизированную акцию, нечем больше вычёркивать из пула"
        );
        assert_eq!(pool[0].symbol, "TSLAUSDT");
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

    // -- time_weighted_median_{bid,ask}_depth_usd_e9 -------------------------

    #[test]
    fn time_weighted_median_weighs_by_duration_to_next_sample() {
        // Один уровень: глубина 10 держится 1с, потом 30 держится 3с — среднее
        // взвешенное (10*1 + 30*3)/4 = 25, не простое среднее (10+30)/2=20.
        // Один уровень на снимок — медиана снимка равна ему самому, порядок
        // операций (Изменение 2) здесь ничего не меняет.
        let samples = vec![
            DepthSample {
                at_ns: 0,
                bid_notional_usd_e9: vec![e9(10)],
                ask_notional_usd_e9: vec![],
            },
            DepthSample {
                at_ns: 1_000_000_000,
                bid_notional_usd_e9: vec![e9(30)],
                ask_notional_usd_e9: vec![],
            },
        ];
        let window_end_ns = 4_000_000_000;
        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(e9(25))
        );
    }

    #[test]
    fn time_weighted_median_of_empty_samples_is_none_not_a_panic() {
        assert_eq!(time_weighted_median_bid_depth_usd_e9(&[], 1_000), None);
        assert_eq!(time_weighted_median_ask_depth_usd_e9(&[], 1_000), None);
    }

    /// Требуемый тест (Изменение 1): толстый бид не должен просочиться в
    /// медиану аска через общее хранилище — раньше `DepthSample` нёс обе
    /// стороны в одном `Vec` (`level_notional_usd_e9`), собранном
    /// `Iterator::chain`, и наблюдение с сотней толстых бидов подняло бы
    /// объединённую медиану выше порога, даже если аск тонок или пуст.
    #[test]
    fn time_weighted_median_does_not_pool_the_two_sides() {
        let samples = vec![DepthSample {
            at_ns: 0,
            bid_notional_usd_e9: vec![DEPTH_FLOOR_USD_E9 * 100; 50], // толстый бид
            ask_notional_usd_e9: vec![e9(1); 50],                    // тонкий аск
        }];
        let window_end_ns = 1_000_000_000;
        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(DEPTH_FLOOR_USD_E9 * 100)
        );
        assert_eq!(
            time_weighted_median_ask_depth_usd_e9(&samples, window_end_ns),
            Some(e9(1)),
            "медиана аска обязана считаться по уровням аска, не смешиваться с бидом"
        );
    }

    /// Требуемый тест (Изменение 2, Decision 18в ревизии 10): порядок
    /// операций — сначала медиана по уровням КАЖДОГО снимка, затем
    /// взвешивание этих скаляров по времени; не наоборот. Числа подобраны
    /// так, что два порядка дают разный ответ на одних и тех же данных: A
    /// (3 уровня, короче) и B (2 уровня) получают равный вес по времени
    /// (1с каждый, window_end = 2с).
    ///
    /// Правильный порядок: median(A=[10,20,30]) = 20, median(B=[1000,2000])
    /// = 1000 + (2000-1000)/2 = 1500; взвешенное среднее по равным весам —
    /// (20 + 1500) / 2 = 760.
    ///
    /// Обратный порядок (сначала взвесить по времени каждую ПОЗИЦИЮ уровня
    /// за оба снимка — с недостающей позицией B[2], учтённой как 0, — и
    /// только потом взять медиану по позициям) даёт другое число: позиции
    /// [505, 1010, 15], медиана 505. Это и есть старое поведение, которое
    /// ревизия 10 отвергла — `assert_ne!` ниже утверждает, что реализация
    /// не должна давать этот ответ.
    #[test]
    fn time_weighted_median_computes_per_snapshot_median_before_time_weighting() {
        let samples = vec![
            DepthSample {
                at_ns: 0,
                bid_notional_usd_e9: vec![e9(10), e9(20), e9(30)],
                ask_notional_usd_e9: vec![],
            },
            DepthSample {
                at_ns: 1_000_000_000,
                bid_notional_usd_e9: vec![e9(1000), e9(2000)],
                ask_notional_usd_e9: vec![],
            },
        ];
        let window_end_ns = 2_000_000_000;

        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(e9(760)),
            "медиана каждого снимка обязана считаться первой, до взвешивания по времени"
        );
        assert_ne!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(e9(505)),
            "505 — ответ обратного (отвергнутого ревизией 10) порядка операций"
        );
    }

    /// Раньше короткий снимок дополнялся нулём на недостающих ПОЗИЦИЯХ
    /// уровня, потому что порядок операций был обратным (см. предыдущий
    /// тест). В правильном порядке (Decision 18в) у каждого снимка — своя
    /// медиана по своим же уровням, дополнять нечем: снимок короче на один
    /// уровень просто даёт медиану по тому, что в нём есть, не паникует и
    /// не голосует «нулевым» уровнем, которого на бирже не было.
    #[test]
    fn time_weighted_median_computes_each_snapshot_independently_without_padding() {
        let samples = vec![
            DepthSample {
                at_ns: 0,
                bid_notional_usd_e9: vec![e9(10), e9(20)],
                ask_notional_usd_e9: vec![],
            },
            DepthSample {
                at_ns: 1,
                bid_notional_usd_e9: vec![e9(25)], // короче на один уровень
                ask_notional_usd_e9: vec![],
            },
        ];
        // median(A=[10,20]) = 10 + (20-10)/2 = 15; median(B=[25]) = 25.
        // Равные веса (window_end=2, dtA=dtB=1): (15+25)/2 = 20.
        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, 2),
            Some(e9(20))
        );
    }

    // -- select_final_two -----------------------------------------------------

    /// Оба борта равны `depth` — совместимо со старым однозначным чтением
    /// большинства тестов ниже, которые не проверяют асимметрию сторон.
    /// Асимметричные сценарии используют `measured_sided` напрямую.
    fn measured(
        symbol: &str,
        depth: i64,
        turnover: i64,
        events: i64,
        window_secs: i64,
    ) -> MeasuredCandidate {
        measured_sided(symbol, depth, depth, turnover, events, window_secs)
    }

    fn measured_sided(
        symbol: &str,
        bid_depth: i64,
        ask_depth: i64,
        turnover: i64,
        events: i64,
        window_secs: i64,
    ) -> MeasuredCandidate {
        MeasuredCandidate {
            symbol: symbol.to_string(),
            window_start_utc_ms: 0,
            window_secs,
            events,
            median_bid_depth_usd_e9: bid_depth,
            median_ask_depth_usd_e9: ask_depth,
            reported_turnover_usd_e9: turnover,
        }
    }

    /// Требуемый тест (Изменение 1, Decision 18б ревизии 10): порог обязан
    /// выполняться на КАЖДОЙ стороне отдельно — толстый бид не может
    /// прикрыть тонкий аск. Бид далеко выше порога, аск далеко ниже — со
    /// старым (объединённым по обеим сторонам) прочтением такой кандидат
    /// мог пройти отбор, потому что усреднённая по сотне уровней глубина
    /// оставалась бы выше порога даже с пустым или крайне тонким аском.
    #[test]
    fn select_final_two_requires_the_depth_floor_on_both_sides_independently() {
        let m = vec![measured_sided(
            "FAT_BID_THIN_ASK",
            DEPTH_FLOOR_USD_E9 * 10,
            DEPTH_FLOOR_USD_E9 / 10,
            0,
            1,
            3600,
        )];
        assert_eq!(
            select_final_two(&m).unwrap_err(),
            PickError::NoSurvivorsAboveDepthFloor {
                floor_usd_e9: DEPTH_FLOOR_USD_E9,
                candidates: 1
            }
        );
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
        median_bid_depth_usd_e9: Option<i64>,
        median_ask_depth_usd_e9: Option<i64>,
        above_depth_floor: Option<bool>,
        selected_for_pilot: bool,
        final_rank: Option<u8>,
    }

    /// Требуемый тест: `write_candidate_table_csv` — единственное место, где
    /// строится файл, названный в done-condition шага 0.4 («таблица
    /// кандидатов ... закоммичена»), и до этого теста ни один тест файла не
    /// доходил до настоящего `csv::Writer`. Четыре строки — по одной на
    /// каждую стадию воронки Decision 18, от «не прошёл даже предфильтр» до
    /// «отобран пилоту»:
    /// - `MID0USDT` — измеренный, прошёл порог, отобран, `final_rank = 1`;
    /// - `MID1USDT` — измеренный, прошёл порог, но НЕ отобран
    ///   (`selected_for_pilot = false`, `final_rank = None`);
    /// - `MID2USDT` — прошёл предфильтр (`prefiltered = true`), но остался
    ///   БЕЗ измерения (`measured = false`) — реальный, не синтетический
    ///   случай: `measure_prefiltered` разослала по сокету на каждый из
    ///   `PREFILTER_TOP_N` кандидатов, но не для всех из них в `measured`
    ///   попал результат (соединение оборвалось, символ не набрал ни одного
    ///   события за час и т. п.) — таких строк без этой стадии в наборе не
    ///   было ни одной;
    /// - `TAIL0USDT` — целиком `None`/`false` (предфильтр отсеял его до
    ///   сети) — та ветка, что молча ломается первой, если формат столбцов
    ///   когда-нибудь разойдётся со структурой.
    ///
    /// **Почему трёх строк (без `MID2USDT`) было недостаточно**, хотя
    /// прежняя версия этого docstring уже утверждала, что перепутанные
    /// местами булевы колонки не прошли бы тест незамеченными: `prefiltered`
    /// и `measured` совпадали в каждой из трёх строк (`true,true` /
    /// `true,true` / `false,false`) — колонка, которую нечем отличить от
    /// соседней в каждой строке, где обе стоят рядом, обменом местами не
    /// повреждается: перепутанные `prefiltered`↔`measured` читаются назад
    /// как исходные значения именно потому, что они везде одинаковы. `MID1`
    /// разводил `selected_for_pilot` с этой парой, но саму пару друг с
    /// другом — нет. `MID2USDT` — единственная строка, где `prefiltered` и
    /// `measured` расходятся (`true`/`false`), и только с ней перестановка
    /// этих двух столбцов действительно меняет то, что читается назад.
    /// Правило на будущее для этого теста: у каждой пары полей одного типа
    /// должна быть хотя бы одна строка, где их значения различаются — иначе
    /// обмен местами такой пары для теста не отличим от отсутствия ошибки.
    ///
    /// `median_bid_depth_usd_e9` и `median_ask_depth_usd_e9` тоже различны
    /// внутри каждой измеренной строки (Изменение 1: раздельные колонки по
    /// стороне) — иначе перепутанные местами бид и аск тоже прошли бы тест.
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
                median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(1)),
                median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(2)),
                above_depth_floor: Some(true),
                selected_for_pilot: true,
                final_rank: Some(1),
            },
            CandidateRow {
                symbol: "MID1USDT".to_string(),
                turnover_24h_usd_e9: e9(900_000),
                tercile: "middle",
                prefiltered: true,
                measured: true,
                window_start_utc_ms: Some(1_700_000_003_600_000),
                window_secs: Some(3600),
                events: Some(999),
                median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(3)),
                median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(4)),
                above_depth_floor: Some(true),
                selected_for_pilot: false,
                final_rank: None,
            },
            CandidateRow {
                symbol: "MID2USDT".to_string(),
                turnover_24h_usd_e9: e9(500_000),
                tercile: "middle",
                prefiltered: true,
                measured: false,
                window_start_utc_ms: None,
                window_secs: None,
                events: None,
                median_bid_depth_usd_e9: None,
                median_ask_depth_usd_e9: None,
                above_depth_floor: None,
                selected_for_pilot: false,
                final_rank: None,
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
                median_bid_depth_usd_e9: None,
                median_ask_depth_usd_e9: None,
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
                    median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(1)),
                    median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(2)),
                    above_depth_floor: Some(true),
                    selected_for_pilot: true,
                    final_rank: Some(1),
                },
                CandidateRowOwned {
                    symbol: "MID1USDT".to_string(),
                    turnover_24h_usd_e9: e9(900_000),
                    tercile: "middle".to_string(),
                    prefiltered: true,
                    measured: true,
                    window_start_utc_ms: Some(1_700_000_003_600_000),
                    window_secs: Some(3600),
                    events: Some(999),
                    median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(3)),
                    median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(4)),
                    above_depth_floor: Some(true),
                    selected_for_pilot: false,
                    final_rank: None,
                },
                CandidateRowOwned {
                    symbol: "MID2USDT".to_string(),
                    turnover_24h_usd_e9: e9(500_000),
                    tercile: "middle".to_string(),
                    prefiltered: true,
                    measured: false,
                    window_start_utc_ms: None,
                    window_secs: None,
                    events: None,
                    median_bid_depth_usd_e9: None,
                    median_ask_depth_usd_e9: None,
                    above_depth_floor: None,
                    selected_for_pilot: false,
                    final_rank: None,
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
                    median_bid_depth_usd_e9: None,
                    median_ask_depth_usd_e9: None,
                    above_depth_floor: None,
                    selected_for_pilot: false,
                    final_rank: None,
                },
            ],
            "неизмеренный кандидат обязан вернуться как None на всех Option-полях, \
             а не как 0, false или пустая строка, принятая за None; и \
             `prefiltered` с `measured` обязаны читаться назад раздельно (MID2USDT: \
             prefiltered=true, measured=false)"
        );
    }

    /// Требуемый тест: `write_instruments_csv` — то, что done-condition шага
    /// 0.4 называет «`instruments.csv` непуст». Круглый путь через
    /// `format_e9` (не `parse_e9`, отдельная копия — см. её doc) проверяется
    /// здесь на реальном файле, а не только опосредованно через
    /// `format_e9_round_trips_through_parse_e9`.
    ///
    /// Изменение 5: `tick_size`, `min_order_qty`, `qty_step` и
    /// `min_notional_value` — четыре РАЗНЫХ числа, попарно не равных. Раньше
    /// `min_order_qty_e9` и `qty_step_e9` были одним и тем же значением
    /// (0.1 == 0.1): перепутанные местами колонки `min_order_qty`/`qty_step`
    /// в `write_instruments_csv` (или в `InstrumentRow`) прошли бы тест
    /// незамеченными, потому что оба столбца читались бы как «0.1» в любом
    /// порядке.
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
            min_order_qty_e9: 500_000_000,
            qty_step_e9: 250_000_000,
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
                min_order_qty: "0.5".to_string(),
                qty_step: "0.25".to_string(),
                min_notional_value: "5".to_string(),
            }]
        );
    }

    // -- book_level_notionals_usd_e9 — по одной аллокации на сторону --------

    /// Требуемый тест (Изменение 1): раньше эта функция принимала обе
    /// стороны разом (`Iterator::chain` бид+аск в один вектор) — Decision
    /// 18(б), ревизия 10, запрещает объединять стороны, и функция теперь
    /// читает ровно одну. `alloc_count::measure` — тот же счётчик, что уже
    /// используют `stats/mod.rs::
    /// the_replication_loop_does_not_allocate_per_replicate`,
    /// `bybit::clock.rs` и `binlog/mod.rs`: он бы не заметил регресс до
    /// `Vec::extend` или до чтения не той стороны, если бы измерялось
    /// только количество элементов.
    #[test]
    fn book_level_notionals_usd_e9_reads_only_the_requested_side_with_one_allocation() {
        use crate::book::{Book, Side, Update};

        let mut book = Book::new(1, 1);
        book.apply(&Update {
            is_snapshot: true,
            u: 1,
            seq: 1,
            cts_ms: 0,
            bids: vec![(100, 5), (99, 3), (98, 1)],
            asks: vec![(101, 2), (102, 4)],
        })
        .unwrap();

        let (bid_levels, bid_counts) =
            crate::alloc_count::measure(|| book_level_notionals_usd_e9(&book, Side::Bid, 1, 1));
        assert_eq!(bid_levels.len(), 3, "три уровня бида, ни одного аска");
        assert_eq!(bid_counts.allocations, 1);

        let (ask_levels, ask_counts) =
            crate::alloc_count::measure(|| book_level_notionals_usd_e9(&book, Side::Ask, 1, 1));
        assert_eq!(ask_levels.len(), 2, "два уровня аска, ни одного бида");
        assert_eq!(ask_counts.allocations, 1);
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
    ///
    /// Изменение 4: сам вызов обёрнут в `tokio::time::timeout`, а не голый
    /// `.await`. Регрессия («дедлайн игнорируется и считается заново от
    /// текущего момента») заставляет `measure_one_symbol` блокироваться на
    /// `rx.recv()` без сети и без собственного ограничения по времени —
    /// без внешнего `timeout` тест тогда не падает красным, а виснет, и CI
    /// читает зависший тест как таймаут инфраструктуры (обычный ответ —
    /// ретрай), а не как красный ассерт. Бюджет — секунды, с большим
    /// запасом над тем, что нужно правильному коду (он обязан вернуться
    /// немедленно), но много меньше часа, на который способна растянуть
    /// ожидание регрессия при реальном `MEASUREMENT_WINDOW_SECS`.
    #[tokio::test]
    async fn measure_one_symbol_stops_at_the_shared_deadline_even_if_started_late() {
        let deadline = tokio::time::Instant::now() + Duration::from_millis(40);
        // «Поздний старт»: тем временем, что делит все символы одного
        // замера, уже распорядились — деконнект, бэкофф, планировщик.
        tokio::time::sleep(Duration::from_millis(80)).await;

        let began = tokio::time::Instant::now();
        let result = tokio::time::timeout(
            Duration::from_secs(2),
            measure_one_symbol("SOLUSDT".to_string(), 1, 1, deadline),
        )
        .await;
        let elapsed = began.elapsed();

        let samples = result.expect(
            "measure_one_symbol обязана вернуться немедленно на уже истёкшем \
             дедлайне, а не блокироваться на rx.recv() без таймаута — таймаут \
             этого теста истёк первым, что и есть регрессия, которую он ловит",
        );

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
        // Токенизированных акций в этой вселенной больше нет намеренно
        // (Изменение 3, Decision 18 ревизии 10): Bybit не возвращает их в
        // пуле категории `linear`, и симулировать здесь нечего — см. doc
        // `build_pool` про то, почему в коде нет отдельного фильтра.

        let pool = build_pool(&candidates, NOW_MS);
        assert_eq!(pool.len(), 18);

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

// ---------------------------------------------------------------------------
// Шаг 0.9: шесть недостающих подкоманд. Каждая — тонкая обёртка поверх уже
// протестированной логики своего модуля: CLI-аргументы, вызов, печать
// артефакта, код выхода. Новой бизнес-логики здесь нет; единственная склейка —
// реплей суточных файлов в книгу и уровни (цепочка 1.1 → 1.2 → 2.1 тем же
// кодом), которую делят `levels`, `markout`, `watch` и `pilot`.
// Живые режимы `clock`/`probe` ходят в сеть; `--fixture` прогоняет тот же код
// (замер, цикл, сводка, CSV) на сценарном источнике без сети — ядро этих
// команд покрыто на фейке, живые ≥1000 циклов идут вне песочницы.
// ---------------------------------------------------------------------------

/// Прогрев трекера по умолчанию, мс: 60 минут (`[ASSUMPTION H3]`).
pub const DEFAULT_WARMUP_MS: i64 = 3_600_000;
/// Скользящее окно `repeat_count` по умолчанию, мс: час (шаг 1.1).
pub const DEFAULT_REPEAT_WINDOW_MS: i64 = 3_600_000;
/// Минимум зачтённых `pulled`-уровней гейта G0 (шаг 3.1).
pub const G0_MIN_PULLED: u64 = 200;

// ---------------------------------------------------------------------------
// Общий реплей: суточные файлы символа → записи уровней и срезы середины.
// ---------------------------------------------------------------------------

/// Одни сутки UTC после реплея: записи уровней и срезы середины.
struct ReplayDay {
    day: String,
    records: Vec<LevelRecord>,
    mids: Vec<MidSample>,
}

/// Итог реплея символа: сутки плюс счётчики для замера GC (байт на запись).
struct ReplayStats {
    days: Vec<ReplayDay>,
    bytes: u64,
    records: u64,
}

/// Рабочее состояние одних суток: книга и конвертер пересоздаются на каждый
/// файл (каждый начинается со снапшота), трекер живёт все части суток —
/// окно `repeat_count` и прогрев считаются по суткам, а не по частям файла.
struct DayWork {
    day: String,
    records: Vec<LevelRecord>,
    mids: Vec<MidSample>,
    tracker: LevelTracker,
}

fn is_trade_ev(ev: u64) -> bool {
    ev == LOCAL_BUY_TRADE_EVENT || ev == LOCAL_SELL_TRADE_EVENT
}

/// Трейд записи в трейд трекера. Отображение повторяет контракт писателя
/// (`record.rs::stage_trade`: `ev` из стороны агрессора, `ival = 1` —
/// блочная) и читателя (`verify.rs`: блочность из `ival`); своей трактовки
/// битов здесь нет.
fn trade_hit_from_record(rec: &Record) -> Option<TradeHit> {
    if !is_trade_ev(rec.ev) {
        return None;
    }
    Some(TradeHit {
        tick: rec.price_ticks,
        lots: rec.qty_lots,
        aggressor_is_buy: rec.ev == LOCAL_BUY_TRADE_EVENT,
        block: rec.ival != 0,
        exch_ms: rec.exch_ts_ns / 1_000_000,
    })
}

/// Сутки из имени файла `<SYMBOL>-<день>[-pN].binlog`: первые 10 знаков
/// остатка. Формат проверяет позже `watch` (`BadDay`), здесь только нарезка.
fn day_of_filename(prefix: &str, name: &str) -> Option<String> {
    let rest = name.strip_prefix(prefix)?.strip_suffix(".binlog")?;
    if rest.len() < 10 {
        return None;
    }
    Some(rest[..10].to_string())
}

/// Хронологический ключ файла: день, затем часть суток. То же правило, что
/// `export::part_order_key` (голая лексикография ставит `-p2` раньше начала
/// суток): имя после префикса — либо день, либо день с `-pN`.
fn file_order_key(prefix: &str, name: &str) -> (String, u32) {
    let rest = name.strip_prefix(prefix).unwrap_or(name);
    let rest = rest.strip_suffix(".binlog").unwrap_or(rest);
    if let Some(tail) = rest.get(10..) {
        if let Some(num) = tail.strip_prefix("-p") {
            if let Ok(part) = num.parse::<u32>() {
                return (rest[..10].to_string(), part);
            }
        }
    }
    (rest.to_string(), 1)
}

/// Применяет обновление к книге и кормит трекер кадром обеих сторон плюс
/// срезом середины. Вызывается только после успешного `apply`.
fn feed_frames(
    book: &Book,
    tracker: &mut LevelTracker,
    ts_ms: i64,
    out: &mut Vec<LevelRecord>,
    mids: &mut Vec<MidSample>,
) {
    for side in [Side::Bid, Side::Ask] {
        let obs: Vec<LevelObs> = book
            .levels(side)
            .enumerate()
            .map(|(i, (tick, lots))| LevelObs {
                tick,
                size_lots: lots,
                in_top50: i < 50,
            })
            .collect();
        tracker.observe_frame(ts_ms, side, &obs, out);
    }
    if let (Some(bid), Some(ask)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
        mids.push(MidSample {
            ts_ms,
            bid_tick: bid,
            ask_tick: ask,
        });
    }
}

/// Реплей всех суточных файлов символа тем же кодом, что файловый `verify`:
/// `Reader` читает кадры, `FileReplayer` группирует записи в обновления,
/// `Book` их применяет, `LevelTracker` развешивает уровни и трейды.
/// Разрыв последовательности (`Err` из `apply`) останавливает файл, как в
/// `verify`: дальше этот файл недоверен, следующий идёт с чистого листа.
fn replay_symbol(root: &Path, symbol: &str, cfg: LevelsConfig) -> anyhow::Result<ReplayStats> {
    let prefix = format!("{symbol}-");
    let entries = std::fs::read_dir(root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", root.display()))?;
    let mut files: Vec<PathBuf> = Vec::new();
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога: {e}"))?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(&prefix) && name.ends_with(".binlog") {
            files.push(e.path());
        }
    }
    files.sort_by(|a, b| {
        let key = |p: &PathBuf| {
            p.file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        };
        file_order_key(&prefix, &key(a)).cmp(&file_order_key(&prefix, &key(b)))
    });
    if files.is_empty() {
        anyhow::bail!("нет суточных файлов {prefix}*.binlog в {}", root.display());
    }
    let mut stats = ReplayStats {
        days: Vec::new(),
        bytes: 0,
        records: 0,
    };
    let mut work: Vec<DayWork> = Vec::new();
    for path in &files {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day = day_of_filename(&prefix, &name)
            .ok_or_else(|| anyhow::anyhow!("имя {name} не разбирается как сутки"))?;
        let data = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
        stats.bytes += data.len() as u64;
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let header = reader.header();
        if work.last().is_none_or(|w| w.day != day) {
            work.push(DayWork {
                day,
                records: Vec::new(),
                mids: Vec::new(),
                tracker: LevelTracker::new(cfg),
            });
        }
        let entry = work.last_mut().expect("только что добавлен");
        let mut book = Book::new(header.tick_e9, header.step_e9);
        let mut replayer = FileReplayer::new();
        let mut ups = Vec::new();
        let mut tps = Vec::new();
        let mut file_ok = true;
        loop {
            let frame = reader
                .read_frame()
                .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
            let Some(frame_records) = frame else { break };
            stats.records += frame_records.len() as u64;
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
                debug_assert_eq!(
                    tps.len(),
                    usize::from(hit.is_some()),
                    "разметка сделок обязана совпадать с FileReplayer"
                );
                for up in &ups {
                    if book.apply(up).is_err() {
                        file_ok = false;
                        break;
                    }
                    feed_frames(
                        &book,
                        &mut entry.tracker,
                        up.cts_ms,
                        &mut entry.records,
                        &mut entry.mids,
                    );
                }
                if !file_ok {
                    break;
                }
                if let Some(h) = hit {
                    entry.tracker.observe_trade(h);
                }
            }
            if !file_ok {
                break;
            }
        }
        if file_ok {
            let mut tail = Vec::new();
            replayer.finish(&mut tail);
            for up in &tail {
                if book.apply(up).is_err() {
                    break;
                }
                feed_frames(
                    &book,
                    &mut entry.tracker,
                    up.cts_ms,
                    &mut entry.records,
                    &mut entry.mids,
                );
            }
        }
    }
    stats.days = work
        .into_iter()
        .map(|w| ReplayDay {
            day: w.day,
            records: w.records,
            mids: w.mids,
        })
        .collect();
    Ok(stats)
}

fn side_name(side: Side) -> &'static str {
    match side {
        Side::Bid => "bid",
        Side::Ask => "ask",
    }
}

fn outcome_name(outcome: Outcome) -> &'static str {
    match outcome {
        Outcome::Eaten => "eaten",
        Outcome::Pulled => "pulled",
        Outcome::Mixed => "mixed",
    }
}

fn death_name(death: DeathKind) -> &'static str {
    match death {
        DeathKind::BelowFraction => "below_fraction",
        DeathKind::LeftTop => "left_top",
    }
}

fn some_or_empty(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.6}")).unwrap_or_default()
}

// ---------------------------------------------------------------------------
// `lob clock` (шаг 0.5).
// ---------------------------------------------------------------------------

/// Аргументы `lob clock`: смещение часов хоста против NTP и `serverTime`,
/// строка в `clock.csv`. Живой каденс шага — раз в час; команда снимает
/// `--samples` замеров подряд и выходит (часовая петля — дело рекордера).
#[derive(Debug, Args)]
pub struct ClockArgs {
    /// Корень записи: сюда дописывается `clock.csv`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// NTP-сервер с портом.
    #[arg(long, default_value = "pool.ntp.org:123")]
    pub ntp_server: String,
    /// REST-хост Bybit v5 для `serverTime`.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// Сколько замеров снять подряд.
    #[arg(long, default_value_t = 1)]
    pub samples: u64,
    /// Фикстура без сети: сценарные раунды через те же `sample`/`append_row`.
    #[arg(long, default_value_t = false)]
    pub fixture: bool,
}

/// Тикер на N тактов: решение «когда закончить» снаружи (Decision 21 отдаёт
/// его `watch`), здесь только счётчик для `clock::run`.
struct CountTicker {
    remaining: u64,
}

impl crate::bybit::clock::Ticker for CountTicker {
    fn next_tick(&mut self) -> bool {
        if self.remaining == 0 {
            return false;
        }
        self.remaining -= 1;
        true
    }
}

/// Сценарный эталон для `--fixture`: та же роль, что `ScriptedSource` в
/// тестах `clock`, — готовые раунды вместо сети.
struct FixtureClock {
    trips: std::collections::VecDeque<RoundTrip>,
}

impl ReferenceClock for FixtureClock {
    fn round_trip(&mut self) -> Result<RoundTrip, crate::bybit::clock::ClockError> {
        self.trips.pop_front().ok_or_else(|| {
            crate::bybit::clock::ClockError::Transport("фикстура исчерпана".to_string())
        })
    }
}

fn fixture_trips(samples: u64) -> std::collections::VecDeque<RoundTrip> {
    (0..samples)
        .map(|i| {
            let base = 1_000_000_000 + i as i64 * 3_600_000_000_000;
            RoundTrip {
                local_send_ns: base,
                remote_ns: base + 1_000_000,
                local_recv_ns: base + 2_000_000,
            }
        })
        .collect()
}

/// Один часовой замер: раунд у обоих эталонов через общий `clock::run`,
/// строка в `clock.csv`. Возвращает строки для проверки `check_rows`.
pub fn run_clock(args: &ClockArgs) -> anyhow::Result<Vec<ClockRow>> {
    let path = args.root.join("clock.csv");
    let mut ticker = CountTicker {
        remaining: args.samples,
    };
    if args.fixture {
        let mut ntp = FixtureClock {
            trips: fixture_trips(args.samples),
        };
        let mut bybit = FixtureClock {
            trips: fixture_trips(args.samples),
        };
        return run_clock_loop(&path, &SystemClock, &mut ntp, &mut bybit, &mut ticker)
            .map_err(|e| anyhow::anyhow!("clock.csv: {e:?}"));
    }
    let mut ntp = UdpNtpSource::connect(args.ntp_server.as_str(), Duration::from_secs(10))
        .map_err(|e| anyhow::anyhow!("NTP {}: {e:?}", args.ntp_server))?;
    let mut bybit = BybitServerTimeSource::new(args.base_url.clone())
        .map_err(|e| anyhow::anyhow!("serverTime: {e:?}"))?;
    run_clock_loop(&path, &SystemClock, &mut ntp, &mut bybit, &mut ticker)
        .map_err(|e| anyhow::anyhow!("clock.csv: {e:?}"))
}

// ---------------------------------------------------------------------------
// `lob probe` (шаг 6.2, Decision 12).
// ---------------------------------------------------------------------------

/// Аргументы `lob probe`: ≥1000 циклов post-only минимального размера далеко
/// от середины, распределение RTT, медиана и p95. Середину читает вызывающий
/// с живого стакана и передаёт готовым числом (модуль зонда тик не читает).
#[derive(Debug, Args)]
pub struct ProbeArgs {
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Сторона ордера: `buy` или `sell`.
    #[arg(long, default_value = "buy")]
    pub side: String,
    /// `lotSizeFilter.minOrderQty` инструмента (Decision 22).
    #[arg(long)]
    pub qty_e9: i64,
    /// `priceFilter.tickSize` инструмента.
    #[arg(long)]
    pub tick_e9: i64,
    /// Смещение цены от середины в тиках («далеко» — больше спреда).
    #[arg(long, default_value_t = 100)]
    pub ticks_from_mid: i64,
    /// Окно подписи Bybit по умолчанию — пять секунд (документация биржи).
    #[arg(long, default_value_t = DEFAULT_RECV_WINDOW_MS)]
    pub recv_window_ms: u32,
    /// Число циклов, не меньше `MIN_CYCLES` (done-condition шага 6.2).
    #[arg(long, default_value_t = MIN_CYCLES)]
    pub cycles: usize,
    /// Середина книги в момент решения, в 1e-9.
    #[arg(long)]
    pub mid_e9: i64,
    /// REST-хост Bybit v5 (приватные запросы).
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// Корень для `probe-<symbol>.csv` по умолчанию.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Куда писать строки циклов (по умолчанию `<root>/probe-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Фикстура без сети и без ключей на бирже: консервные ответы через те же
    /// `run_cycles`/`summarize`. Подпись считается настоящим кодом.
    #[arg(long, default_value_t = false)]
    pub fixture: bool,
}

fn parse_probe_side(s: &str) -> anyhow::Result<OrderSide> {
    match s.to_ascii_lowercase().as_str() {
        "buy" => Ok(OrderSide::Buy),
        "sell" => Ok(OrderSide::Sell),
        other => Err(anyhow::anyhow!(
            "сторона обязана быть buy или sell, получено {other}"
        )),
    }
}

/// Консервный транспорт для `--fixture`: успешное создание и успешная отмена
/// с непустым `orderId` — тот же контракт ответа, что ждёт `run_cycle`.
struct FixturePrivateRest;

impl PrivateRest for FixturePrivateRest {
    fn send(&mut self, req: SignedRequest) -> Result<String, ProbeError> {
        match req.path {
            "/v5/order/create" | "/v5/order/cancel" => Ok(
                "{\"retCode\":0,\"retMsg\":\"OK\",\"result\":{\"orderId\":\"fixture-1\"},\"retExtInfo\":{},\"time\":1}"
                    .to_string(),
            ),
            other => Err(ProbeError::Decode(format!(
                "фикстура не знает путь {other}"
            ))),
        }
    }
}

/// Прогон зонда: `run_cycles` тем же кодом, что живой замер, сводка —
/// `summarize`, строки циклов — в CSV. Меньше `MIN_CYCLES` — отказ тем же
/// вариантом ошибки, что `probe::probe`.
pub fn run_probe(args: &ProbeArgs) -> anyhow::Result<(RttSummary, PathBuf)> {
    if args.cycles < MIN_CYCLES {
        return Err(anyhow::anyhow!(
            "{:?}",
            ProbeError::TooFewCycles {
                requested: args.cycles,
                minimum: MIN_CYCLES,
            }
        ));
    }
    let creds = Credentials::from_env()
        .map_err(|e| anyhow::anyhow!("ключи: {e:?} — выставьте BYBIT_API_KEY/BYBIT_API_SECRET"))?;
    let params = ProbeParams {
        symbol: args.symbol.clone(),
        side: parse_probe_side(&args.side)?,
        qty_e9: args.qty_e9,
        tick_e9: args.tick_e9,
        ticks_from_mid: args.ticks_from_mid,
        recv_window_ms: args.recv_window_ms,
    };
    let mid = args.mid_e9;
    let cycles = if args.fixture {
        let mut fx = FixturePrivateRest;
        run_cycles(&mut fx, &creds, &params, || mid, args.cycles)
            .map_err(|e| anyhow::anyhow!("{e:?}"))?
    } else {
        let mut rest =
            BybitPrivateRest::new(args.base_url.clone()).map_err(|e| anyhow::anyhow!("{e:?}"))?;
        run_cycles(&mut rest, &creds, &params, || mid, args.cycles)
            .map_err(|e| anyhow::anyhow!("{e:?}"))?
    };
    let summary = summarize(&cycles);
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("probe-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(["cycle", "rtt_ns"])?;
    for (i, c) in cycles.iter().enumerate() {
        w.write_record([i.to_string(), c.rtt_ns().to_string()])?;
    }
    w.flush()?;
    Ok((summary, out))
}

// ---------------------------------------------------------------------------
// `lob levels` (шаги 1.1, 1.2).
// ---------------------------------------------------------------------------

/// Аргументы `lob levels`: читает суточные файлы, пишет уровни с шестью
/// признаками истории и классом исхода. Порог `H3` — параметр без умолчания:
/// выдуманного числа здесь быть не должно, значение предрегистрируется.
#[derive(Debug, Args)]
pub struct LevelsArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Порог рождения H3 в лотах: размер строго больше.
    #[arg(long)]
    pub h3_lots: i64,
    /// Прогрев в мс: рождения раньше него отслеживаются, но не печатаются.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать уровни (по умолчанию `<root>/levels-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
}

/// Итог `lob levels` для печати диспетчером.
pub struct LevelsSummary {
    pub days: usize,
    pub levels: usize,
    pub out: PathBuf,
}

/// Разметка символа реплеем из `replay_symbol` — тем же кодом, что
/// подтверждающий прогон, — и запись уровней с классом в CSV.
pub fn run_levels(args: &LevelsArgs) -> anyhow::Result<LevelsSummary> {
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("levels-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record([
        "day_utc",
        "side",
        "price_tick",
        "birth_ms",
        "death_ms",
        "lifetime_ms",
        "size_max",
        "time_to_max_ms",
        "size_monotonic",
        "repeat_count",
        "repriced",
        "death",
        "traded_lots",
        "outcome",
    ])?;
    let mut n = 0usize;
    for day in &replay.days {
        for r in &day.records {
            w.write_record([
                day.day.clone(),
                side_name(r.side).to_string(),
                r.price_tick.to_string(),
                r.birth_ms.to_string(),
                r.death_ms.to_string(),
                r.lifetime_ms.to_string(),
                r.size_max.to_string(),
                r.time_to_max_ms.to_string(),
                r.size_monotonic.to_string(),
                r.repeat_count.to_string(),
                r.repriced.to_string(),
                death_name(r.death).to_string(),
                r.traded_lots.to_string(),
                outcome_name(r.outcome()).to_string(),
            ])?;
            n += 1;
        }
    }
    w.flush()?;
    Ok(LevelsSummary {
        days: replay.days.len(),
        levels: n,
        out,
    })
}

// ---------------------------------------------------------------------------
// `lob markout` (шаг 2.1, Decision 14).
// ---------------------------------------------------------------------------

/// Аргументы `lob markout`: `m` на четырёх горизонтах по Decision 14.
/// С `--confirmatory` читает ровно сутки из `ready.flag` и без флага
/// отказывается стартовать ненулевым кодом (Decision 21): это механическая
/// защита от optional stopping, а не обещание не подглядывать.
#[derive(Debug, Args)]
pub struct MarkoutArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Порог рождения H3 в лотах (тот же, что у `levels`).
    #[arg(long)]
    pub h3_lots: i64,
    /// Прогрев в мс.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать markout (по умолчанию `<root>/markout-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Подтверждающий режим: сутки ровно из флага, без флага — отказ.
    #[arg(long, default_value_t = false)]
    pub confirmatory: bool,
    /// Путь к `ready.flag` (по умолчанию `<root>/ready.flag`).
    #[arg(long)]
    pub flag: Option<PathBuf>,
    /// Медиана `lifetime_ms` популяции C1 по первым суткам (Decision 16):
    /// нужна только в `--confirmatory` для счётчиков суток.
    #[arg(long)]
    pub median_lifetime_ms: Option<i64>,
}

/// Итог `lob markout` для печати диспетчером.
pub struct MarkoutSummary {
    pub days: usize,
    pub levels: usize,
    pub out: PathBuf,
    pub horizon_line: String,
}

/// Среднее доступных значений горизонта одной строкой: отсутствие данных —
/// `n/a`, а не ноль (нет будущего — не нулевой сдвиг).
fn horizon_stat(name: &str, values: &[f64]) -> String {
    if values.is_empty() {
        return format!("{name}: n/a");
    }
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    format!("{name}: n={} mean={mean:.3}bps", values.len())
}

fn write_markout_csv(out: &Path, days: &[ReplayDay]) -> anyhow::Result<(usize, [Vec<f64>; 4])> {
    debug_assert_eq!(
        HORIZONS_MS,
        [100, 1_000, 10_000, 60_000],
        "порядок колонок обязан совпадать с горизонтами"
    );
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(out)?;
    w.write_record([
        "day_utc",
        "side",
        "price_tick",
        "birth_ms",
        "death_ms",
        "outcome",
        "m_100ms",
        "m_1s",
        "m_10s",
        "m_60s",
    ])?;
    let mut n = 0usize;
    let mut per_horizon: [Vec<f64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for day in days {
        for r in &day.records {
            let ms = markouts_for_level(r, &day.mids);
            for (i, slot) in per_horizon.iter_mut().enumerate() {
                if let Some(v) = ms[i] {
                    slot.push(v);
                }
            }
            w.write_record([
                day.day.clone(),
                side_name(r.side).to_string(),
                r.price_tick.to_string(),
                r.birth_ms.to_string(),
                r.death_ms.to_string(),
                outcome_name(r.outcome()).to_string(),
                some_or_empty(ms[0]),
                some_or_empty(ms[1]),
                some_or_empty(ms[2]),
                some_or_empty(ms[3]),
            ])?;
            n += 1;
        }
    }
    w.flush()?;
    Ok((n, per_horizon))
}

fn horizon_line(per_horizon: &[Vec<f64>; 4]) -> String {
    format!(
        "{}; {}; {}; {}",
        horizon_stat("m_100ms", &per_horizon[0]),
        horizon_stat("m_1s", &per_horizon[1]),
        horizon_stat("m_10s", &per_horizon[2]),
        horizon_stat("m_60s", &per_horizon[3]),
    )
}

/// Разведочный markout: все сутки корня, флаг не требуется и не читается.
fn run_markout_exploratory(args: &MarkoutArgs) -> anyhow::Result<MarkoutSummary> {
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("markout-{}.csv", args.symbol)));
    let (n, per_horizon) = write_markout_csv(&out, &replay.days)?;
    Ok(MarkoutSummary {
        days: replay.days.len(),
        levels: n,
        out,
        horizon_line: horizon_line(&per_horizon),
    })
}

/// Подтверждающий markout: первым делом флаг (без него — отказ до всякого
/// markout), дальше ровно сутки из списка флага через общий
/// `markup::run_confirmatory`. Счётчики суток — тем же `tally_day`, что
/// `watch`: предикат годности один на весь документ.
fn run_markout_confirmatory(args: &MarkoutArgs) -> anyhow::Result<MarkoutSummary> {
    let flag_path = args
        .flag
        .clone()
        .unwrap_or_else(|| ready_flag_path(&args.root));
    // Первым делом флаг, до всякого markout: без него — отказ, а не пустая
    // выборка (тот же порядок, что `markup::run_confirmatory`).
    let flag = require_ready_flag(&flag_path).map_err(|e| anyhow::anyhow!("{e}"))?;
    let median = args.median_lifetime_ms.ok_or_else(|| {
        anyhow::anyhow!("--confirmatory требует --median-lifetime-ms (Decision 16)")
    })?;
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let tallies = day_tallies(&args.root, &args.symbol, &replay.days, median)?;
    let mut cdays: Vec<ConfirmatoryDay<'_>> = Vec::new();
    for want in &flag.days {
        let day = replay
            .days
            .iter()
            .find(|d| &d.day == want)
            .ok_or_else(|| anyhow::anyhow!("сутки {want} из флага не найдены среди реплея"))?;
        let tally = tallies
            .iter()
            .find(|t| &t.day_utc == want)
            .ok_or_else(|| anyhow::anyhow!("счётчик суток {want} не собрался"))?;
        cdays.push(ConfirmatoryDay {
            tally,
            records: &day.records,
            mids: &day.mids,
        });
    }
    let report = run_confirmatory(&flag_path, &cdays).map_err(|e| anyhow::anyhow!("{e}"))?;
    let out = args.out.clone().unwrap_or_else(|| {
        args.root
            .join(format!("markout-confirmatory-{}.csv", args.symbol))
    });
    let selected: Vec<ReplayDay> = replay
        .days
        .into_iter()
        .filter(|d| flag.days.iter().any(|w| w == &d.day))
        .collect();
    let (n, _) = write_markout_csv(&out, &selected)?;
    Ok(MarkoutSummary {
        days: report.days_used.len(),
        levels: n,
        out,
        horizon_line: report.summary_line(),
    })
}

/// Точка входа `lob markout`: разведочный режим пишет `m` на четырёх
/// горизонтах, подтверждающий — идёт через флаг и вердикт G1.
pub fn run_markout(args: &MarkoutArgs) -> anyhow::Result<MarkoutSummary> {
    if args.confirmatory {
        run_markout_confirmatory(args)
    } else {
        run_markout_exploratory(args)
    }
}

// ---------------------------------------------------------------------------
// Счётчики суток для `watch` и подтверждающего `markout`.
// ---------------------------------------------------------------------------

/// Собирает `DayTally` из реплея тем же `tally_day`, что читает G2.
/// Качество суток — из файлов корня: `gaps.csv` (разрыв > 6 часов) и
/// `verify.csv` шага 0.8 (расхождения теста 1). Отсутствующие файлы — ноль
/// строк, а не ошибка: сутки без проверок негодны по правилу `day_eligible`
/// (ноль проверок — не годно), и это честный красный, а не падение команды.
///
/// Правила отображения (консервативные, задокументированы здесь, а не
/// размазаны по вызывающим):
/// - разрыв: любая строка `SequenceGap`/`LowSpace` этих суток и символа —
///   сутки с разрывом (длительности в строке нет, поэтому любое такое
///   событие суток считается старшим);
/// - verify: знаменатель — строки с решённым сравнением (`Ok`/`Mismatch`),
///   числитель — строки `Mismatch`; `Misaligned`/`RestUnavailable` —
///   неопределённые, как `trades_indeterminate` в `verify`.
fn day_tallies(
    root: &Path,
    symbol: &str,
    days: &[ReplayDay],
    median_lifetime_ms: i64,
) -> anyhow::Result<Vec<DayTally>> {
    let gaps = read_gap_rows(&gaps_csv_path(root)).map_err(|e| anyhow::anyhow!("gaps.csv: {e}"))?;
    let verify_rows =
        read_verify_rows(&verify_csv_path(root)).map_err(|e| anyhow::anyhow!("verify.csv: {e}"))?;
    days.iter()
        .map(|day| {
            let gap = gaps.iter().any(|g| {
                g.symbol == symbol
                    && g.ts_utc.starts_with(&day.day)
                    && matches!(g.kind, GapKind::SequenceGap | GapKind::LowSpace)
            });
            let mut basis = 0u64;
            let mut violations = 0u64;
            for row in verify_rows
                .iter()
                .filter(|r| r.symbol == symbol && r.ts_utc.starts_with(&day.day))
            {
                match row.verdict {
                    VerifyVerdict::Ok => basis += 1,
                    VerifyVerdict::Mismatch => {
                        basis += 1;
                        violations += 1;
                    }
                    VerifyVerdict::Misaligned | VerifyVerdict::RestUnavailable => {}
                }
            }
            Ok(tally_day(
                symbol,
                &day.day,
                &day.records,
                median_lifetime_ms,
                gap,
                violations,
                basis,
            ))
        })
        .collect()
}

// ---------------------------------------------------------------------------
// `lob watch` (шаг 4.1, Decision 21).
// ---------------------------------------------------------------------------

/// Аргументы `lob watch`: считает `n` и `G` для C2, пишет `progress.csv` и
/// выставляет `ready.flag`. Markout не вычисляет ни в каком виде: видит
/// только счётчики через `tally_day`/`WatchState`.
#[derive(Debug, Args)]
pub struct WatchArgs {
    /// Корень записи: суточные файлы, `progress.csv`, `ready.flag`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ подтверждающей записи (H10: один на всё состояние).
    #[arg(long)]
    pub symbol: String,
    /// Порог рождения H3 в лотах (тот же, что у `levels`).
    #[arg(long)]
    pub h3_lots: i64,
    /// Прогрев в мс.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Медиана `lifetime_ms` популяции C1 по первым суткам (Decision 16).
    #[arg(long)]
    pub median_lifetime_ms: i64,
    /// Момент выставления флага строкой UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
}

/// Итог `lob watch` для печати диспетчером.
pub struct WatchSummary {
    pub days: usize,
    pub n_c2: u64,
    pub g_c2: u64,
    /// Сутки выборки через запятую, если флаг выставлен этим прогоном.
    pub flag: Option<String>,
    pub progress: PathBuf,
}

/// Суточный шаг целиком тем же кодом, что живая запись: принять сутки,
/// переписать прогресс, при срабатывании триггера выставить флаг — ровно
/// один раз (`WatchState::observe_day`).
pub fn run_watch(args: &WatchArgs) -> anyhow::Result<WatchSummary> {
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let tallies = day_tallies(
        &args.root,
        &args.symbol,
        &replay.days,
        args.median_lifetime_ms,
    )?;
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let mut state = WatchState::new(&args.symbol, args.median_lifetime_ms);
    let mut flag: Option<String> = None;
    for tally in &tallies {
        let outcome = state
            .observe_day(&args.root, tally.clone(), &now)
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        if let Some(f) = outcome.flag {
            flag = Some(f.days.join(","));
        }
    }
    Ok(WatchSummary {
        days: tallies.len(),
        n_c2: state.n_c2_total(),
        g_c2: state.g_c2(),
        flag,
        progress: progress_csv_path(&args.root),
    })
}

// ---------------------------------------------------------------------------
// `lob pilot` (шаг 3.1) и гейт G0.
// ---------------------------------------------------------------------------

/// Аргументы `lob pilot`: цепочка 1.1 → 1.2 → 2.1 тем же кодом, что
/// подтверждающий прогон, сверка комиссий с H4, замер байт на запись,
/// вердикт G0 и строка в `runs.csv`. CPU и RSS — только живой замер
/// (шаг 3.1): на фикстуре печатаются как `live-only`, в вердикт не входят.
#[derive(Debug, Args)]
pub struct PilotArgs {
    /// Корень записи: суточные файлы кандидата.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Кандидат, например `SOLUSDT` (по строке на кандидата, H10).
    #[arg(long)]
    pub symbol: String,
    /// Порог рождения H3 в лотах (тот же, что у `levels`).
    #[arg(long)]
    pub h3_lots: i64,
    /// Прогрев в мс.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Комиссия мейкера в bps: обязана совпасть с H4, иначе стоп.
    #[arg(long, default_value_t = MAKER_FEE_BPS)]
    pub maker_fee_bps: f64,
    /// Комиссия тейкера в bps: обязана совпасть с H4, иначе стоп.
    #[arg(long, default_value_t = TAKER_FEE_BPS)]
    pub taker_fee_bps: f64,
    /// Журнал прогонов (канонический путь шага 7.1).
    #[arg(long, default_value = "docs/plan/runs.csv")]
    pub runs_out: PathBuf,
    /// Момент строки журнала UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
}

/// Итог `lob pilot` для печати диспетчером.
pub struct PilotSummary {
    pub symbol: String,
    pub n_pulled: u64,
    pub mean_m_10s_bps: f64,
    pub mean_net_bps: f64,
    pub verdict: String,
    pub runs_out: PathBuf,
}

/// Пилот тем же кодом, что подтверждающий прогон: реплей → `is_c1` (шаг 1.2
/// поверх 1.1) → markout на 10 с (шаг 2.1) → издержки Decision 15 на каждом
/// наблюдении отдельно. Гейт G0 дословно: ≥200 зачтённых `pulled`-уровней
/// **и** средний `m` на 10 с не ниже полных круговых издержек — 7.5 bps
/// комиссий плюс проскальзывание. Сравнение идёт через средний net
/// (`mean_net_bps >= 0`): среднее разностей равно разности средних на тех же
/// наблюдениях, а строгость к отсутствующим данным наследуется вызовом.
pub fn run_pilot(args: &PilotArgs) -> anyhow::Result<PilotSummary> {
    if args.maker_fee_bps != MAKER_FEE_BPS || args.taker_fee_bps != TAKER_FEE_BPS {
        anyhow::bail!(
            "комиссии сменились (мейкер {} тейкер {} против H4 {MAKER_FEE_BPS}/{TAKER_FEE_BPS}): предположение H4 требует перепроверки, пилот остановлен",
            args.maker_fee_bps,
            args.taker_fee_bps
        );
    }
    debug_assert_eq!(
        HORIZONS_MS[2], 10_000,
        "индекс горизонта 10 с обязан указывать на 10 000 мс"
    );
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let mut observations: Vec<Observation> = Vec::new();
    let mut m_sum = 0.0f64;
    for day in &replay.days {
        for rec in &day.records {
            if !is_c1(rec) {
                continue;
            }
            let Some(m) = markouts_for_level(rec, &day.mids)[2] else {
                continue;
            };
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
            let Some(x) = exit else { continue };
            observations.push(Observation {
                m_bps: m,
                spread_ticks_exit: x.ask_tick - x.bid_tick,
                mid2x_base: base2x,
            });
            m_sum += m;
        }
    }
    let n_pulled = observations.len() as u64;
    let mean_m = if n_pulled > 0 {
        m_sum / n_pulled as f64
    } else {
        0.0
    };
    let mean_net = mean_net_bps(&observations);
    let verdict = if n_pulled < G0_MIN_PULLED {
        format!("G0 RED (sparse): зачтено {n_pulled} pulled при минимуме {G0_MIN_PULLED}")
    } else {
        match mean_net {
            Some(v) if v.is_finite() && v >= 0.0 => {
                format!("G0 pass: {n_pulled} pulled, средний net {v:.3}bps >= 0")
            }
            _ => "G0 RED: средний net ниже полных круговых издержек".to_string(),
        }
    };
    let mean_net_print = mean_net.unwrap_or(0.0);
    let bytes_per_record = if replay.records > 0 {
        replay.bytes as f64 / replay.records as f64
    } else {
        0.0
    };
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let detail = format!(
        "n_pulled={n_pulled} mean_m_10s_bps={mean_m:.3} mean_net_bps={mean_net_print:.3} fees_bps={ROUNDTRIP_FEES_BPS} verdict={verdict} bytes_per_record={bytes_per_record:.1} cpu=rss=live-only"
    );
    log_pilot_run(&args.runs_out, &args.symbol, &now, &detail)
        .map_err(|e| anyhow::anyhow!("runs.csv: {e}"))?;
    Ok(PilotSummary {
        symbol: args.symbol.clone(),
        n_pulled,
        mean_m_10s_bps: mean_m,
        mean_net_bps: mean_net_print,
        verdict,
        runs_out: args.runs_out.clone(),
    })
}

#[cfg(test)]
mod ticket09_tests {
    use super::*;
    use crate::binlog::{Header, Writer};
    use crate::bybit::verify_sidecar::{append_verify_row, VerifyRow};
    use crate::lob::runs::{read_run_rows, RunKind};
    use hftbacktest::types::{
        LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
        LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    };

    const FIX_TICK_E9: i64 = 10_000_000; // 0.01
    const FIX_STEP_E9: i64 = 1_000_000; // 0.001

    fn test_header() -> Header {
        Header {
            tick_e9: FIX_TICK_E9,
            step_e9: FIX_STEP_E9,
            max_records_per_frame: 4096,
        }
    }

    fn depth_rec(ev: u64, ts_ms: i64, tick: i64, lots: i64) -> Record {
        Record {
            ev,
            exch_ts_ns: ts_ms * 1_000_000,
            local_ts_ns: ts_ms * 1_000_000 + 500_000,
            price_ticks: tick,
            qty_lots: lots,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    fn snap_frame(ts_ms: i64, bids: &[(i64, i64)], asks: &[(i64, i64)]) -> Vec<Record> {
        let mut out = Vec::new();
        for &(tick, lots) in bids {
            out.push(depth_rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, ts_ms, tick, lots));
        }
        for &(tick, lots) in asks {
            out.push(depth_rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, ts_ms, tick, lots));
        }
        out
    }

    fn delta_frame(ts_ms: i64, bids: &[(i64, i64)], asks: &[(i64, i64)]) -> Vec<Record> {
        let mut out = Vec::new();
        for &(tick, lots) in bids {
            out.push(depth_rec(LOCAL_BID_DEPTH_EVENT, ts_ms, tick, lots));
        }
        for &(tick, lots) in asks {
            out.push(depth_rec(LOCAL_ASK_DEPTH_EVENT, ts_ms, tick, lots));
        }
        out
    }

    fn write_day(root: &Path, symbol: &str, day: &str, frames: &[Vec<Record>]) {
        let mut w = Writer::create(Vec::new(), test_header(), 1).unwrap();
        for f in frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        let buf = w.into_inner();
        std::fs::write(root.join(format!("{symbol}-{day}.binlog")), &buf).unwrap();
    }

    fn levels_args(root: &Path) -> LevelsArgs {
        LevelsArgs {
            root: root.to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            out: None,
        }
    }

    /// Три уровня: съеден (ровно 70% — граница `eaten`), смешанный, снят.
    /// Лучший бид нигде не догоняет лучший аск: книга не пересекается.
    fn three_level_frames() -> Vec<Vec<Record>> {
        vec![
            snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(105, 10)]),
            vec![
                depth_rec(LOCAL_SELL_TRADE_EVENT, 500, 98, 7),
                depth_rec(LOCAL_SELL_TRADE_EVENT, 500, 99, 5),
            ],
            delta_frame(1000, &[(96, 10), (98, 1), (99, 1), (100, 1)], &[(105, 10)]),
            delta_frame(2000, &[(96, 1), (98, 1), (99, 1), (100, 1)], &[(105, 10)]),
        ]
    }

    #[test]
    fn lob_help_lists_all_ten_subcommands() {
        #[derive(clap::Parser)]
        struct TestCli {
            #[command(subcommand)]
            cmd: LobCommand,
        }
        use clap::CommandFactory;
        let mut top = TestCli::command();
        top.build();
        let mut sorted: Vec<String> = top
            .get_subcommands()
            .map(|s| s.get_name().to_string())
            .filter(|n| n != "help")
            .collect();
        sorted.sort();
        let expected = [
            "clock", "export", "levels", "markout", "pick", "pilot", "probe", "record", "verify",
            "watch",
        ];
        assert_eq!(
            sorted,
            expected.iter().map(ToString::to_string).collect::<Vec<_>>()
        );
    }

    #[test]
    fn clock_fixture_writes_a_row() {
        let dir = tempfile::tempdir().unwrap();
        let args = ClockArgs {
            root: dir.path().to_path_buf(),
            ntp_server: "127.0.0.1:1".to_string(),
            base_url: "http://127.0.0.1:1".to_string(),
            samples: 2,
            fixture: true,
        };
        let rows = run_clock(&args).unwrap();
        assert_eq!(rows.len(), 2);
        let read_back = crate::bybit::clock::read_rows(&dir.path().join("clock.csv")).unwrap();
        assert_eq!(read_back.len(), 2);
        assert_eq!(read_back[0].ntp_offset_ns, Some(0));
        assert!(check_rows(&read_back).is_empty());
    }

    fn probe_args(root: &Path, cycles: usize) -> ProbeArgs {
        ProbeArgs {
            symbol: "SOLUSDT".to_string(),
            side: "buy".to_string(),
            qty_e9: 1_000_000,
            tick_e9: FIX_TICK_E9,
            ticks_from_mid: 100,
            recv_window_ms: 5000,
            cycles,
            mid_e9: 100_000_000_000,
            base_url: "http://127.0.0.1:1".to_string(),
            root: root.to_path_buf(),
            out: None,
            fixture: true,
        }
    }

    #[test]
    fn probe_fixture_writes_distribution() {
        crate::bybit::sign::with_cleared_env(|| {
            std::env::set_var(crate::bybit::sign::API_KEY_VAR, "fixture");
            std::env::set_var(crate::bybit::sign::API_SECRET_VAR, "fixture");
            let dir = tempfile::tempdir().unwrap();
            let (summary, out) = run_probe(&probe_args(dir.path(), 1000)).unwrap();
            assert_eq!(summary.n, 1000);
            assert!(summary.median_ns > 0);
            assert!(summary.p95_ns >= summary.median_ns);
            assert!(out.exists());
        });
    }

    #[test]
    fn probe_rejects_fewer_than_1000_cycles() {
        let dir = tempfile::tempdir().unwrap();
        assert!(run_probe(&probe_args(dir.path(), 10)).is_err());
    }

    #[test]
    fn levels_fixture_writes_classified_levels() {
        let dir = tempfile::tempdir().unwrap();
        write_day(dir.path(), "SOLUSDT", "2026-09-08", &three_level_frames());
        let summary = run_levels(&levels_args(dir.path())).unwrap();
        assert_eq!(summary.levels, 4);
        let text = std::fs::read_to_string(&summary.out).unwrap();
        for col in [
            "lifetime_ms",
            "size_max",
            "time_to_max_ms",
            "size_monotonic",
            "repeat_count",
            "repriced",
        ] {
            assert!(text.contains(col), "нет колонки {col}");
        }
        for class in ["eaten", "mixed", "pulled"] {
            assert!(text.contains(class), "нет класса {class}");
        }
    }

    #[test]
    fn markout_fixture_writes_four_horizons() {
        let dir = tempfile::tempdir().unwrap();
        let mut frames = three_level_frames();
        // Хвост середин до 72 с теми же размерами: ни рождений, ни смертей,
        // но будущие срезы для всех горизонтов есть.
        for k in 1..=7 {
            frames.push(delta_frame(
                2000 + k * 10_000,
                &[(96, 1), (98, 1), (99, 1), (100, 1)],
                &[(105, 10)],
            ));
        }
        write_day(dir.path(), "SOLUSDT", "2026-09-08", &frames);
        let args = MarkoutArgs {
            root: dir.path().to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            out: None,
            confirmatory: false,
            flag: None,
            median_lifetime_ms: None,
        };
        let summary = run_markout(&args).unwrap();
        assert_eq!(summary.levels, 4);
        let mut reader = csv::Reader::from_path(&summary.out).unwrap();
        let headers = reader.headers().unwrap().clone();
        let col = |name: &str| {
            headers
                .iter()
                .position(|h| h == name)
                .unwrap_or_else(|| panic!("нет колонки {name}"))
        };
        let (m10, tick_col) = (col("m_10s"), col("price_tick"));
        for name in ["m_100ms", "m_1s", "m_10s", "m_60s"] {
            col(name);
        }
        let mut seen_10s = false;
        for rec in reader.records() {
            let rec = rec.unwrap();
            if &rec[tick_col] == "98" {
                let v: f64 = rec[m10].parse().expect("у уровня 98 обязан быть m на 10 с");
                assert!(v.is_finite());
                seen_10s = true;
            }
        }
        assert!(seen_10s, "строка уровня 98 не найдена");
    }

    #[test]
    fn markout_confirmatory_without_flag_is_err() {
        let dir = tempfile::tempdir().unwrap();
        let args = MarkoutArgs {
            root: dir.path().to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            out: None,
            confirmatory: true,
            flag: None,
            median_lifetime_ms: Some(60_000),
        };
        assert!(run_markout(&args).is_err());
        assert!(dispatch(LobCommand::Markout(args)).is_err());
    }

    /// Двенадцать суток по 12 рождений на одной цене внутри часа: повторы
    /// 0..11, C2 (повтор ≥ 2) — 9 зачтённых в сутки, итого n=108, G=12.
    fn watch_day_frames() -> Vec<Vec<Record>> {
        let mut frames = vec![snap_frame(0, &[(100, 10)], &[(101, 10)])];
        for i in 1..=11i64 {
            frames.push(delta_frame(i * 180_000 - 60_000, &[(100, 1)], &[(101, 10)]));
            frames.push(delta_frame(i * 180_000, &[(100, 10)], &[(101, 10)]));
        }
        frames
    }

    #[test]
    fn watch_fixture_writes_progress_and_flag() {
        let dir = tempfile::tempdir().unwrap();
        let frames = watch_day_frames();
        for d in 0..12 {
            let day = format!("2026-09-{:02}", 8 + d);
            write_day(dir.path(), "SOLUSDT", &day, &frames);
            append_verify_row(
                &crate::bybit::verify_sidecar::verify_csv_path(dir.path()),
                &VerifyRow {
                    ts_utc: format!("{day}T00:05:00Z"),
                    symbol: "SOLUSDT".to_string(),
                    snapshot_seq: Some(1),
                    book_seq: Some(1),
                    mismatches: Some(0),
                    verdict: VerifyVerdict::Ok,
                },
            )
            .unwrap();
        }
        let args = WatchArgs {
            root: dir.path().to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            median_lifetime_ms: 3_600_000,
            now_utc: Some("2026-09-20T00:00:00Z".to_string()),
        };
        let summary = run_watch(&args).unwrap();
        assert_eq!(summary.days, 12);
        assert_eq!(summary.n_c2, 108);
        assert_eq!(summary.g_c2, 12);
        assert!(summary.flag.is_some());
        assert!(dir.path().join("ready.flag").exists());
        let flag = require_ready_flag(&dir.path().join("ready.flag")).unwrap();
        assert_eq!(flag.n_c2, 108);
        assert_eq!(flag.g, 12);
        let progress = std::fs::read_to_string(&summary.progress).unwrap();
        assert_eq!(progress.lines().count(), 13, "шапка плюс строка на сутки");
    }

    /// Скользящая лесенка: лучший бид падает 2 тика/с, спред 2 тика.
    /// Кадры 1..=129 хоронят по 2 `pulled`-бида; ушедшие уровни несут
    /// size 0 (как пишет рекордер), иначе книга пересеклась бы. У смертей
    /// до 119 с есть будущее на 10 с — 240 зачтённых при нисходящем тренде.
    fn pilot_frames() -> Vec<Vec<Record>> {
        let mut frames = Vec::new();
        for k in 0..130i64 {
            let best = 1000 - 2 * k;
            let mut bids: Vec<(i64, i64)> = (0..10).map(|j| (best - j, 10)).collect();
            let mut asks = vec![(best + 2, 10)];
            if k > 0 {
                // Ушедшие наверх тики явно удаляются нулевым размером.
                bids.push((best + 2, 0));
                bids.push((best + 1, 0));
                asks.push((best + 4, 0));
            }
            let ts = k * 1000;
            frames.push(if k == 0 {
                snap_frame(ts, &bids, &asks)
            } else {
                delta_frame(ts, &bids, &asks)
            });
        }
        frames
    }

    fn pilot_args(root: &Path) -> PilotArgs {
        PilotArgs {
            root: root.to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            maker_fee_bps: 2.0,
            taker_fee_bps: 5.5,
            runs_out: root.join("runs.csv"),
            now_utc: Some("2026-09-08T00:00:00Z".to_string()),
        }
    }

    #[test]
    fn pilot_fixture_writes_verdict_and_runs_row() {
        let dir = tempfile::tempdir().unwrap();
        write_day(dir.path(), "SOLUSDT", "2026-09-08", &pilot_frames());
        let summary = run_pilot(&pilot_args(dir.path())).unwrap();
        assert_eq!(summary.n_pulled, 258);
        assert!(
            summary.verdict.starts_with("G0 pass"),
            "{}",
            summary.verdict
        );
        let rows = read_run_rows(&dir.path().join("runs.csv")).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].kind, RunKind::Pilot);
        assert_eq!(rows[0].symbol, "SOLUSDT");
        assert!(rows[0].detail.contains("G0 pass"));
    }

    #[test]
    fn pilot_rejects_changed_fees() {
        let dir = tempfile::tempdir().unwrap();
        let mut args = pilot_args(dir.path());
        args.taker_fee_bps = 6.0;
        assert!(run_pilot(&args).is_err());
    }
}
