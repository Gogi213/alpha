//! `lob pick` — правило пула Decision 25: `--top` бессрочных USDT-контрактов
//! (по умолчанию десять, В-35), старших по обороту за 24 часа, после трёх
//! исключений (шаг 0.4). Чистая
//! функция `build_pool` и её вход (`CandidateMeta` — метаданные инструмента,
//! склеенные с тикером через `join_candidate_meta`) не делают ввода-вывода
//! вообще и проверены тестами ниже исчерпывающе, в том числе на вырожденных
//! входах — см. doc модуля `super` про разрез на файлы.
//!
//! Покрытие топ-50 в bps и пригодность корзин расстояния (Decision 26а) —
//! `super::coverage`, отдельная ответственность от самого правила отбора.

use std::collections::HashMap;

use crate::bybit::rest::{Instrument, Ticker};

use super::coverage::coverage_top50_bps;

pub const MIN_LISTED_DAYS: i64 = 30;
const MS_PER_DAY: i64 = 24 * 60 * 60 * 1_000;

/// Размер пула по умолчанию — Decision 25/В-35: первые десять оставшихся после
/// трёх исключений, не «первая десятка минус выбывшие», иначе размер пула
/// плавал бы от того, сколько неподходящих случайно оказалось наверху.
///
/// С 2026-09-13 это **умолчание**, а не константа: владелец («раскатка на
/// топ 50», В-50) разрешил брать больше, и размер приходит параметром `--top`;
/// число строк `instruments.csv` равно ему.
pub const POOL_SIZE: usize = 10;

const LINEAR_QUOTE_COIN: &str = "USDT";
const LINEAR_CONTRACT_TYPE: &str = "LinearPerpetual";

// ---------------------------------------------------------------------------
// Стадия 1: метаданные инструмента + оборот → вход пула
// ---------------------------------------------------------------------------

/// Всё, что известно про инструмент **до** часового замера глубины: то, что
/// приносят `instruments-info` и `tickers` (`bybit::rest`), склеенные по
/// символу. Оборот — для ранжирования пула, шаг цены и последняя цена — для
/// покрытия топ-50 шага 0.4 (`50 × tickSize / цена × 10⁴`): без них ни
/// покрытие, ни пригодность корзин Decision 26а не считаются.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CandidateMeta {
    pub symbol: String,
    pub base_coin: String,
    pub quote_coin: String,
    pub contract_type: String,
    /// `None` — Bybit не прислал `launchTime` (см. `rest::Instrument`).
    pub launch_time_ms: Option<i64>,
    pub turnover_24h_usd_e9: i64,
    pub tick_e9: i64,
    pub last_price_e9: i64,
    /// `lotSizeFilter` из `instruments-info` — вход размера-22а: без них ни
    /// размер, ни его номинал для колонки таблицы не считаются.
    pub min_order_qty_e9: i64,
    pub qty_step_e9: i64,
    pub min_notional_value_e9: i64,
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

/// Склеивает метаданные инструмента с его тикером. Символ без тикера
/// отбрасывается — без оборота ранжировать нечем, а без цены не считается
/// покрытие шага 0.4; включать его в пул значило бы дать ему место без данных.
/// Это не одно из трёх исключений Decision 25 (у него нет `excluded_reason`),
/// а отсутствие данных: строка без оборота и цены в таблице непроверяема.
pub fn join_candidate_meta(instruments: &[Instrument], tickers: &[Ticker]) -> Vec<CandidateMeta> {
    let by_symbol: HashMap<&str, &Ticker> =
        tickers.iter().map(|t| (t.symbol.as_str(), t)).collect();
    instruments
        .iter()
        .filter_map(|inst| {
            let ticker = by_symbol.get(inst.symbol.as_str())?;
            Some(CandidateMeta {
                symbol: inst.symbol.clone(),
                base_coin: inst.base_coin.clone(),
                quote_coin: inst.quote_coin.clone(),
                contract_type: inst.contract_type.clone(),
                launch_time_ms: inst.launch_time_ms,
                turnover_24h_usd_e9: ticker.turnover_24h_usd_e9,
                tick_e9: inst.tick_e9,
                last_price_e9: ticker.last_price_e9,
                min_order_qty_e9: inst.min_order_qty_e9,
                qty_step_e9: inst.qty_step_e9,
                min_notional_value_e9: inst.min_notional_value_e9,
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Стадия 2: пул Decision 25 — десять старших по обороту после трёх исключений
// ---------------------------------------------------------------------------

/// Один инструмент пула: прошедший все три исключения и вошедший в первые
/// десять оставшихся по обороту. Шаг цены и последняя цена едут дальше —
/// по ним `build_candidate_table` считает покрытие топ-50 шага 0.4.
#[derive(Debug, Clone, PartialEq)]
pub struct PoolCandidate {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
    pub tick_e9: i64,
    pub last_price_e9: i64,
    /// `lotSizeFilter` из `instruments-info` (через `CandidateMeta`) — вход
    /// размера-22а и колонки номинала таблицы. Едут в пуле тем же приёмом,
    /// что шаг цены и последняя цена для покрытия: без них размер на
    /// инструмент не посчитать.
    pub min_order_qty_e9: i64,
    pub qty_step_e9: i64,
    pub min_notional_value_e9: i64,
    /// Покрытие топ-50 в bps (`50 × tickSize / цена × 10⁴`, шаг 0.4).
    /// `None` — делить не на что (неположительный шаг или цена): ноль здесь
    /// читался бы как «книги нет», а не «цены нет».
    pub coverage_top50_bps: Option<f64>,
}

/// Причина исключения — колонка `excluded_reason` шага 0.4: строка на каждое
/// исключение, потому что признака некриптового актива в API нет и решение
/// принимается здесь, а не на бирже.
pub const EXCLUDED_BTC_ETH: &str = "btc_eth_by_name";
/// Базовый актив не криптоактив (пункт 2 Decision 25).
pub const EXCLUDED_NON_CRYPTO: &str = "non_crypto_base";
/// Возраст контракта < 30 суток (пункт 3 Decision 25).
pub const EXCLUDED_TOO_YOUNG: &str = "listed_under_30d";
/// Прошёл все три исключения, но не вошёл в первые десять по обороту.
/// Не исключение по правилу, а срез ранжирования — без этой строки из
/// таблицы нельзя проверить, что пул это действительно *первые* десять
/// оставшихся, а не десять произвольных. Имя кода отличает его от трёх
/// `EXCLUDED_*` намеренно (таск 27): `excluded_reason` несёт исключения по
/// правилам §2 и этот срез ранга — и ничего больше; глубина в эту колонку
/// не попадает никогда, у неё своя `depth_check` (BUSINESS-TASK §9).
pub const RANK_BEYOND_POOL: &str = "rank_beyond_pool";

/// Некриптовые базовые активы (пункт 2 Decision 25: акции, ETF, металлы).
/// У Bybit нет поля, отличающего их от крипты: проверено 2026-09-10,
/// `AAPLUSDT` это `contractType: LinearPerpetual` со `status: Trading`, ровно
/// как `SOLUSDT` — поэтому `SETTLED.md` ПЛАН-2, утверждавший, что категория
/// `linear` такие контракты не возвращает, **неверен**, и решение принимается
/// здесь, по базовому активу.
///
/// Состав списка и его происхождение, честно, потому что альтернативы списку
/// нет (BUSINESS-TASK.md, раздел 2): `AAPL`, `SNDK` — акции, `SOXL` — ETF,
/// `XAU` — золото из замера 2026-09-10, записанного в Decision 25 и
/// BUSINESS-TASK §2; `TSLA` — акция, пример токенизированной акции из истории
/// этого шага; `CL` — `CLUSDT`, тикер нефти WTI (спека, «Открытые места»:
/// умолчание владельца — исключить до подтверждения одним словом). Список
/// неполон по построению: новый листинг некриптового актива пройдёт фильтр,
/// пока его базу не впишут сюда — это цена отсутствия признака в API, а не
/// недосмотр реализации.
pub const NON_CRYPTO_BASES: &[&str] = &["AAPL", "SNDK", "SOXL", "XAU", "TSLA", "CL"];

/// Один инструмент, не вошедший в пул, с причиной — строка таблицы
/// кандидатов шага 0.4. Оборот печатается и у исключённых: иначе из таблицы
/// нельзя проверить ранжирование («первые десять *оставшихся*»).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExcludedCandidate {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
    pub excluded_reason: &'static str,
}

/// Итог стадии пула: сам пул (≤ `pool_size`, по убыванию оборота) и все
/// остальные рассмотренные символы с причинами (по убыванию оборота).
#[derive(Debug)]
pub struct PoolOutcome {
    pub pool: Vec<PoolCandidate>,
    pub excluded: Vec<ExcludedCandidate>,
}

/// Строит пул Decision 25: `pool_size` бессрочных USDT-контрактов, старших по
/// обороту за 24 часа **среди прошедших исключения** — первые `pool_size`
/// оставшихся после фильтра, иначе размер пула плавал бы от того, сколько
/// неподходящих случайно оказалось наверху. Исключения: (1) BTC и ETH по
/// имени — прямое указание владельца; (2) некриптовый базовый актив — по
/// `NON_CRYPTO_BASES`, признака в API нет; (3) возраст < 30 суток.
///
/// `pool_size` — параметр, не константа: умолчание `POOL_SIZE` (десять,
/// Decision 25), но владелец разрешил больше (В-50: «раскатка на топ 50»),
/// и число приходит из `--top`. Раньше здесь стояла константа, и это делало
/// размер пула нерешаемым иначе как правкой кода.
///
/// Приоритет причин при совпадении нескольких — в порядке пунктов плана:
/// имя, затем базовый актив, затем возраст. Детерминирован и записан здесь,
/// а не оставлен вызывающему: у символа одна строка в таблице и одна причина.
///
/// Фильтры области (не USDT-котировка, не `LinearPerpetual`) — не исключения
/// Decision 25 и строк не получают: это область запроса `instruments-info`,
/// а не решение шага 0.4. Пул фиксируется на весь прогон: потерявший
/// ликвидность посреди записи остаётся в отчёте, а не заменяется.
/// Причина одного из трёх исключений Decision 25 — или `None`, если
/// кандидат проходит все три. Общая для `build_pool` (размечает всю область)
/// и `base_coins_considered_until_pool_complete` (останавливается на
/// десятом прошедшем): обе обязаны видеть одно и то же решение на одном и
/// том же кандидате, иначе список «увиденных до десятого» разошёлся бы с
/// причинами в таблице.
fn exclusion_reason(c: &CandidateMeta, now_ms: i64) -> Option<&'static str> {
    if c.base_coin == "BTC" || c.base_coin == "ETH" {
        Some(EXCLUDED_BTC_ETH)
    } else if NON_CRYPTO_BASES.contains(&c.base_coin.as_str()) {
        Some(EXCLUDED_NON_CRYPTO)
    } else if !is_listed_long_enough(c.launch_time_ms, now_ms) {
        Some(EXCLUDED_TOO_YOUNG)
    } else {
        None
    }
}

pub fn build_pool(candidates: &[CandidateMeta], now_ms: i64, pool_size: usize) -> PoolOutcome {
    let mut passing: Vec<&CandidateMeta> = Vec::new();
    let mut excluded: Vec<ExcludedCandidate> = Vec::new();
    for c in candidates {
        if c.quote_coin != LINEAR_QUOTE_COIN {
            continue;
        }
        if c.contract_type != LINEAR_CONTRACT_TYPE {
            continue;
        }
        let reason = exclusion_reason(c, now_ms);
        match reason {
            Some(excluded_reason) => excluded.push(ExcludedCandidate {
                symbol: c.symbol.clone(),
                turnover_24h_usd_e9: c.turnover_24h_usd_e9,
                excluded_reason,
            }),
            None => passing.push(c),
        }
    }
    passing.sort_by(|a, b| {
        b.turnover_24h_usd_e9
            .cmp(&a.turnover_24h_usd_e9)
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    let mut pool = Vec::new();
    for (i, c) in passing.iter().enumerate() {
        let coverage_top50_bps = coverage_top50_bps(c.tick_e9, c.last_price_e9);
        if i < pool_size {
            pool.push(PoolCandidate {
                symbol: c.symbol.clone(),
                turnover_24h_usd_e9: c.turnover_24h_usd_e9,
                tick_e9: c.tick_e9,
                last_price_e9: c.last_price_e9,
                coverage_top50_bps,
                min_order_qty_e9: c.min_order_qty_e9,
                qty_step_e9: c.qty_step_e9,
                min_notional_value_e9: c.min_notional_value_e9,
            });
        } else {
            excluded.push(ExcludedCandidate {
                symbol: c.symbol.clone(),
                turnover_24h_usd_e9: c.turnover_24h_usd_e9,
                excluded_reason: RANK_BEYOND_POOL,
            });
        }
    }
    excluded.sort_by(|a, b| {
        b.turnover_24h_usd_e9
            .cmp(&a.turnover_24h_usd_e9)
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    PoolOutcome { pool, excluded }
}

/// Базовые активы **всех** кандидатов, которых правило рассмотрело по пути к
/// десятому выжившему (история 2 спеки, `R28`–`R31`) — не «первые N», а
/// ровно те, кого правило увидело: идёт по той же области (USDT-линейный
/// перп) в том же порядке убывания оборота, что и сам пул, и на каждом шаге
/// проверяет то же самое исключение (`exclusion_reason`), пока не наберёт
/// `pool_size` прошедших все три. Кандидаты ниже него по обороту не входят —
/// они не были нужны, чтобы найти последнего выжившего пула, в отличие от
/// `RANK_BEYOND_POOL` внутри `build_pool`, которая размечает вообще всех
/// прошедших, каким бы низким ни был их оборот. Меньше `pool_size` прошедших
/// во всей области — возвращает всех рассмотренных: последнего не существует.
pub fn base_coins_considered_until_pool_complete(
    candidates: &[CandidateMeta],
    now_ms: i64,
    pool_size: usize,
) -> Vec<String> {
    let mut region: Vec<&CandidateMeta> = candidates
        .iter()
        .filter(|c| c.quote_coin == LINEAR_QUOTE_COIN && c.contract_type == LINEAR_CONTRACT_TYPE)
        .collect();
    region.sort_by(|a, b| {
        b.turnover_24h_usd_e9
            .cmp(&a.turnover_24h_usd_e9)
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    let mut seen = Vec::new();
    let mut passing = 0usize;
    for c in region {
        seen.push(c.base_coin.clone());
        if exclusion_reason(c, now_ms).is_none() {
            passing += 1;
            if passing == pool_size {
                break;
            }
        }
    }
    seen
}

#[cfg(test)]
mod tests;
