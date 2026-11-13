//! Контракт адаптера биржи для ядра коллектора: только специфика биржи — адреса, подписка, пул, разбор кадра.
//!
//! Ядро (`session`, `shard`, `writer`) биржу не знает; адаптеры — `binance.rs`, `bybit.rs`, `okx.rs`.

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
    /// Сколько крупнейших по обороту не брать в пул, если не задано: правило G-022 — Binance и Bybit без топ-10, остальные биржи — весь пул.
    fn default_top_exclude(&self) -> usize {
        10
    }
    /// Метаданные символов пула (размер контракта и т. п.) — в `pool-<день>.json` рядом с пулом; заполняется `load_pool`.
    fn pool_meta(&self) -> Option<serde_json::Value> {
        None
    }
    /// Кадр — предупреждение биржи о плановом разрыве соединения (OKX `notice` 64008): сессия открывает замену и закрывается после перекрытия.
    fn is_retire_notice(&self, _frame: &[u8]) -> bool {
        false
    }
}
