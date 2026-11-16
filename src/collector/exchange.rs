//! Контракт адаптера биржи для ядра коллектора: только специфика биржи — адреса, подписка, пул, разбор кадра.
//!
//! Ядро (`session`, `shard`, `writer`) биржу не знает; адаптеры — `binance.rs`, `bybit.rs`, `okx.rs`, `bingx.rs`, `gate.rs`.

use std::net::IpAddr;
use std::time::Duration;

use super::codec::Rec;

/// Итог разбора кадра: `Recs(n)` — n записей выдано; `Raw` — кадр не разобран точно (ответ на подписку, pong, новые поля,
/// блок-сделка): ядро пишет его как пришёл. Ничего не выдаётся, пока форма кадра не проверена целиком.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Parsed {
    Recs(usize),
    Raw,
}

/// Куда адаптер складывает результат разбора кадра: записи и «хвост», разобрать который точно нельзя.
pub trait Out {
    fn rec(&mut self, r: &Rec<'_>);
    /// Часть кадра, не разобранная точно (элементы массива с неожиданной формой), в кадре той же формы, что и исходный: пишется как `r`.
    fn raw(&mut self, frame: &[u8]);
}

/// Приёмник только записей (хвост отбрасывается): счёт и замеры.
pub struct Recs<F>(pub F);

impl<F: FnMut(&Rec<'_>)> Out for Recs<F> {
    fn rec(&mut self, r: &Rec<'_>) {
        (self.0)(r);
    }

    fn raw(&mut self, _frame: &[u8]) {}
}

/// Разбор кадров одного шарда (состояние — у адаптеров с дельтами, Bybit tickers).
///
/// Массивы (mark Binance, пачка сделок Bybit) разбираются поэлементно: годные элементы — записи, остальные — одним `raw` в кадре той же формы;
/// ничего не теряется и не дублируется. Если кадр не разобрать вовсе (форма, ответ подписки, повреждение) — `Parsed::Raw`, `out` пуст.
pub trait Parser: Send {
    fn parse(&mut self, frame: &[u8], out: &mut dyn Out) -> Parsed;
}

/// Один шард = одно соединение (две линии на тот же набор потоков — задача шарда): имя, эндпоинт биржи, потоки.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ShardPlan {
    pub name: String,
    pub endpoint: String,
    pub streams: Vec<String>,
}

/// Семейство адресов входа: IPv6 у части входов даёт задержки доставки (TK-532, проба входов), IPv4 надёжнее.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Family {
    Any,
    V4,
    V6,
}

/// Вход биржи: имя (SNI/Host), адрес (если закреплён), семейство адресов.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entrance {
    pub host: String,
    pub ip: Option<IpAddr>,
    pub family: Family,
}

impl Entrance {
    pub fn new(host: &str, family: Family) -> Self {
        Self {
            host: host.to_string(),
            ip: None,
            family,
        }
    }

    /// `host[@ip|@v4|@v6]`; без суффикса — семейство `default`.
    pub fn parse(s: &str, default: Family) -> Result<Self, String> {
        let (host, tail) = s.split_once('@').map_or((s, None), |(h, t)| (h, Some(t)));
        if host.is_empty() {
            return Err("пустое имя входа".into());
        }
        let (ip, family) = match tail {
            None => (None, default),
            Some("v4") => (None, Family::V4),
            Some("v6") => (None, Family::V6),
            Some(t) => {
                let ip = t
                    .parse::<IpAddr>()
                    .map_err(|e| format!("адрес входа {t:?}: {e}"))?;
                (Some(ip), if ip.is_ipv4() { Family::V4 } else { Family::V6 })
            }
        };
        Ok(Self {
            host: host.to_string(),
            ip,
            family,
        })
    }
}

/// Все торгуемые контракты биржи с оборотом за сутки, $ (REST) и метаданными контракта: вход для пересмотра пула без перезапуска.
#[derive(Clone, Debug, Default)]
pub struct Turnover {
    pub qv: std::collections::BTreeMap<String, f64>,
    /// Метаданные контракта по символу (размер, шаг и т. п.; пусто у Binance и Bybit).
    pub meta: std::collections::BTreeMap<String, serde_json::Value>,
}

/// Каким числом запись сделки несёт сплошной номер биржи по символу: по нему находятся пропуски (потерянные сделки при сбросе соединений).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TradeSeq {
    /// Запись `a` — один номер (Binance aggTrade id, BingX, Gate).
    A,
    /// Запись покрывает номера `f..=l` (OKX: агрегат из `count` сделок, `tradeId` последней).
    FL,
}

/// Сделка из REST для дозапроса: номер биржи, время, цена и размер строками как пришли (В-172), `f`/`l` — как в записи ws, `m` — покупатель мейкер.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RestTrade {
    pub id: i64,
    pub t_ms: i64,
    pub p: String,
    pub q: String,
    pub f: i64,
    pub l: i64,
    pub m: bool,
}

/// Одна страница дозапроса: сделки внутри запрошенного диапазона и что осталось запросить (`None` — диапазон закрыт: дальше биржа ничего не отдаст).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TradePage {
    pub trades: Vec<RestTrade>,
    pub rest: Option<(i64, i64)>,
}

/// Что осталось запросить после страницы «от новых к старым» (OKX, Gate: запрос «старше номера»): страница полная и самый старый номер в ней выше `lo` — продолжать от него.
pub fn next_range_desc(
    raw_len: usize,
    limit: usize,
    oldest: Option<i64>,
    lo: i64,
) -> Option<(i64, i64)> {
    match oldest {
        Some(o) if raw_len >= limit && o > lo => Some((lo, o - 1)),
        _ => None,
    }
}

/// Лаг прихода, мс: приём (нс) − время биржи (мс); разность в целых нс, затем в мс — точно (f64 не держит нс эпохи).
#[allow(clippy::cast_precision_loss)]
pub fn lag_ms(rx_ns: i64, e_ms: i64) -> f64 {
    rx_ns.saturating_sub(e_ms.saturating_mul(1_000_000)) as f64 / 1e6
}

pub trait Exchange: Send + Sync {
    fn name(&self) -> &'static str;
    /// Виды потоков в порядке запуска.
    fn kinds(&self) -> &'static [&'static str];
    /// Потоков на соединение по умолчанию для вида.
    fn per_conn(&self, kind: &str) -> usize;
    /// Раскладка пула по шардам: `per_conn` — переопределение (0 — по умолчанию вида).
    fn plan(&self, pool: &[String], kinds: &[String], per_conn: usize) -> Vec<ShardPlan>;
    /// Адрес WebSocket для эндпоинта шарда через вход `host`.
    fn url(&self, endpoint: &str, host: &str) -> String;
    /// Входы линий по умолчанию: для каждой линии — основной вход и запасные по порядку.
    fn default_entrances(&self, lanes: usize) -> Vec<Vec<Entrance>>;
    fn subscribe_messages(&self, streams: &[String]) -> Vec<String>;
    /// Сообщения отписки от потоков на живом соединении (пересмотр пула без перезапуска): те же потоки, что в `subscribe_messages`.
    fn unsubscribe_messages(&self, streams: &[String]) -> Vec<String>;
    /// Пауза между сообщениями подписки (лимит входящих сообщений биржи на соединение).
    fn sub_period(&self) -> Duration;
    fn new_parser(&self) -> Box<dyn Parser>;
    /// Лаг прихода кадра, мс (приём − время биржи); `None` — в кадре нет времени биржи.
    fn lag_ms(&self, frame: &[u8], rx_ns: i64) -> Option<f64>;
    /// Ключ символа кадра для «с данными за минуту» (как в кадре; сравнение без учёта регистра — на стороне ядра).
    fn symbol<'a>(&self, frame: &'a [u8]) -> Option<&'a [u8]>;
    /// Пул по правилу G-022 (оборот за сутки ≥ `min_qv`, без `top_exclude` крупнейших): (пул, исключённый топ).
    fn load_pool(
        &self,
        min_qv: f64,
        top_exclude: usize,
    ) -> Result<(Vec<String>, Vec<String>), String>;
    /// Оборот за сутки всех торгуемых контрактов (REST): пул пересматривается по нему раз в период (`livepool`).
    fn load_turnover(&self) -> Result<Turnover, String>;
    /// Сколько крупнейших по обороту не брать в пул, если не задано: ни одного — зеркалим биржу, собираем всё, что торгуется выше порога
    /// (до 16.11 Binance и Bybit исключали топ-10: BARD, AKE, DOGE, NEAR выпадали на сутки и дольше, `docs/findings/collect-gap-audit-2026-11-16.md`).
    fn default_top_exclude(&self) -> usize {
        0
    }
    /// Потоков на шард: предел для докладки символов в живой пул (`--streams-per-conn` — переопределение, 0 — по умолчанию вида).
    fn shard_cap(&self, kind: &str, per_conn: usize) -> usize {
        if per_conn == 0 {
            self.per_conn(kind)
        } else {
            per_conn
        }
    }
    /// Потолок нагрузки на шард, кадров/с (по обеим линиям, как `msgs_10s`): раскладка не кладёт в шард больше, пока есть другой с запасом, а иначе открывает новый шард;
    /// `None` — предел биржи не измерен, раскладка только выравнивает.
    fn shard_rate_cap(&self, _kind: &str) -> Option<f64> {
        None
    }
    /// Виды, чьи потоки не зависят от пула (Binance `mark`: один поток на все символы): символ в них не докладывается и не снимается.
    fn pool_free_kinds(&self) -> &'static [&'static str] {
        &[]
    }
    /// У записей сделок этой биржи есть сплошной номер по символу: писатель ищет пропуски номеров (подтверждённая потеря) и просит дозапрос (`backfill`). `None` — номера нет
    /// (Bybit: UUID) или он не сплошной.
    fn trade_seq(&self) -> Option<TradeSeq> {
        None
    }
    /// Страница дозапроса потерянных сделок `lo..=hi` символа из REST (в событийный цикл не входит: зовёт отдельный поток); `None` — у биржи нет запроса по номеру,
    /// пропуск остаётся потерей.
    fn trade_page(&self, _symbol: &str, _lo: i64, _hi: i64) -> Option<Result<TradePage, String>> {
        None
    }
    /// Метаданные символов пула (размер контракта и т. п.) — в `pool-<день>.json` рядом с пулом; заполняется `load_pool`.
    fn pool_meta(&self) -> Option<serde_json::Value> {
        None
    }
    /// Кадр — предупреждение биржи о плановом разрыве соединения (OKX `notice` 64008): сессия открывает замену и закрывается после перекрытия.
    fn is_retire_notice(&self, _frame: &[u8]) -> bool {
        false
    }
    /// Кадры биржи — двоичные gzip (BingX): сессия разжимает их до метки лага, разбора и записи; остальные биржи шлют текст.
    fn gzip_frames(&self) -> bool {
        false
    }
    /// Кадр — служебный запрос биржи прикладного уровня, на который клиент обязан ответить текстом (BingX: `Ping` раз в 5 с → `Pong`, иначе закрытие
    /// на 30-й секунде): сессия отвечает сразу и кадр дальше не передаёт — это сердцебиение, не данные.
    fn heartbeat_reply(&self, _frame: &[u8]) -> Option<&'static str> {
        None
    }
    /// Дополнительные заголовки запроса WebSocket-рукопожатия (Gate: `X-Gate-Size-Decimal: 1` — иначе размеры приходят числом, усечённым до целого).
    fn ws_headers(&self) -> &'static [(&'static str, &'static str)] {
        &[]
    }
}
