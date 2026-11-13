//! Адаптер BingX public WebSocket (USDT-M swap) для ядра коллектора: адреса, подписка, пул, разбор кадра.
//!
//! Виды (`docs/findings/bingx-ws-prep-2026-11-12.md`): book — `bookTicker` (склеен биржей по времени, ≈ 2,6 записи/с на символ: «тик за тиком» для верха
//! книги у BingX нет), trade — `trade` (пачка 1–8 сделок в кадре), mark — `markPrice` (только цена: индекса и ставки в потоке нет — они пусты, как шлёт биржа).
//! Один эндпоинт `/swap-market` на все виды; подписка по одной теме в сообщении; все кадры — двоичные gzip (ядро разжимает: `gzip_frames`), сердцебиение —
//! текст `Ping` раз в 5 с, ответ `Pong` (ядро: `heartbeat_reply`); кадр данных — `{"code":0,"dataType":"<символ>@<вид>","data":…}`.
//! Разбор строгий: набор ключей точно как в замерах 12.11, иначе элемент (или весь кадр) пишется как пришёл.
//! Личность записи (снятие дублей между линиями и заменами): book — `(символ, T)` (у `bookTicker` счётчик `u` свой на каждом соединении), trade — `t`
//! (id сделок приходят не по порядку у 1–6 %: дубль определяется по множеству виденных id, не по «≤ последнего»), mark — `E`.

use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Map, Value};

use super::codec::{Book, Mark, Rec, Trade};
use super::exchange::{lag_ms, Entrance, Exchange, Family, Out, Parsed, Parser, ShardPlan};
use super::pool::{balanced_chunks, http_get_json_ua, rank_pool};
use super::scan::{each_elem, parse_i64, parse_object, pick, slots};

const REST: &str = "https://open-api.bingx.com/openApi/swap/v2/quote";
const HOST: &str = "open-api-swap.bingx.com";
const PATH: &str = "/swap-market";
const UA: &str = "alpha-collector/bingx";
/// Тем на соединение: максимум биржи (документация и замер W5: 200 приняты, на 201-ю — ответ `80403`, соединение живёт).
pub const MAX_TOPICS: usize = 200;
const META_KEYS: [&str; 10] = [
    "size",
    "quantityPrecision",
    "pricePrecision",
    "tradeMinQuantity",
    "tradeMinUSDT",
    "apiStateOpen",
    "apiStateClose",
    "launchTime",
    "displayName",
    "asset",
];

#[derive(Default)]
pub struct Bingx {
    /// `meta` и `qv` пула (из последнего `load_pool`) — рядом с `pool-<день>.json`.
    extra: Mutex<Option<Value>>,
}

/// Парсер без состояния: записи кадра не зависят от предыдущих.
struct BingxParser;

impl Parser for BingxParser {
    fn parse(&mut self, frame: &[u8], out: &mut dyn Out) -> Parsed {
        parse_frame(frame, out).map_or(Parsed::Raw, Parsed::Recs)
    }
}

/// Кадр той же формы, что исходный, но только с неразобранными элементами `data`.
fn wrap(topic: &[u8], elems: &[u8]) -> Vec<u8> {
    let mut w = Vec::with_capacity(elems.len() + 64);
    w.extend_from_slice(b"{\"code\":0,\"dataType\":\"");
    w.extend_from_slice(topic);
    w.extend_from_slice(b"\",\"data\":");
    w.extend_from_slice(elems);
    w.push(b'}');
    w
}

fn parse_frame(frame: &[u8], out: &mut dyn Out) -> Option<usize> {
    let top = parse_object(frame)?;
    let [code, dt, data] = slots(&top, [b"code", b"dataType", b"data"])?;
    if code.int()? != 0 {
        return None;
    }
    let topic = dt.str()?;
    let at = topic.iter().position(|&c| c == b'@')?;
    let (sym, kind) = (topic.get(..at)?, topic.get(at + 1..)?);
    let data = data.raw()?;
    match kind {
        b"bookTicker" => book(sym, data, out),
        b"markPrice" => mark(sym, data, out),
        b"trade" => trades(topic, sym, data, out),
        _ => None,
    }
}

/// `{"e":"bookTicker","u":…,"E":…,"T":…,"s":…,"b":…,"B":…,"a":…,"A":…}`: E — событие, T — время матчинга; ключ внутри соединения — `u`, между
/// соединениями — `(символ, T)` (запись — по таймеру биржи, `T` растёт строго по символу, замер 12.11: 0 равных подряд на 44 986 парах).
fn book(sym: &[u8], data: &[u8], out: &mut dyn Out) -> Option<usize> {
    let o = parse_object(data)?;
    let [ev, u, e, t, s, bid, bsz, ask, asz] =
        slots(&o, [b"e", b"u", b"E", b"T", b"s", b"b", b"B", b"a", b"A"])?;
    if ev.str()? != b"bookTicker" || s.str()? != sym {
        return None;
    }
    let t = t.int()?;
    out.rec(&Rec::Book(Book {
        sym,
        e: e.int()?,
        t,
        u: u.int()?,
        id: t,
        bid: bid.str()?,
        bsz: bsz.str()?,
        ask: ask.str()?,
        asz: asz.str()?,
        bn: b"",
        an: b"",
    }));
    Some(1)
}

/// `{"e":"markPriceUpdate","E":…,"s":…,"p":…}`: индекса, ставки и времени начисления в потоке нет — столбцы `i`, `r`, `T` пусты (решение координатора:
/// «mark как шлёт биржа»; индекс и фандинг у BingX только по REST `premiumIndex`).
fn mark(sym: &[u8], data: &[u8], out: &mut dyn Out) -> Option<usize> {
    let o = parse_object(data)?;
    let [ev, e, s, p] = slots(&o, [b"e", b"E", b"s", b"p"])?;
    if ev.str()? != b"markPriceUpdate" || s.str()? != sym {
        return None;
    }
    out.rec(&Rec::Mark(Mark {
        sym,
        e: e.int()?,
        p: p.str()?,
        ap: b"",
        pp: b"",
        i: b"",
        r: b"",
        t: 0,
    }));
    Some(1)
}

/// `[{"t":"<id>","q":…,"p":…,"T":…,"m":bool,"s":…}, …]`: годные элементы — записи, негодные — один `raw` в кадре той же формы; ничего не теряется и не
/// дублируется. Ни одного годного — `None` (кадр целиком сырым, `out` пуст). `m` = «покупатель — мейкер» (то есть активная продажа), как в формате.
fn trades(topic: &[u8], sym: &[u8], data: &[u8], out: &mut dyn Out) -> Option<usize> {
    if each_elem(data, |_| true)? == 0 {
        return None;
    }
    let (mut good, mut bad): (usize, Vec<u8>) = (0, Vec::new());
    each_elem(data, |e| {
        if trade(sym, e, out).is_some() {
            good += 1;
        } else {
            bad.push(if bad.is_empty() { b'[' } else { b',' });
            bad.extend_from_slice(e);
        }
        true
    })?;
    if good == 0 {
        return None;
    }
    if !bad.is_empty() {
        bad.push(b']');
        out.raw(&wrap(topic, &bad));
    }
    Some(good)
}

fn trade(sym: &[u8], e: &[u8], out: &mut dyn Out) -> Option<()> {
    let o = parse_object(e)?;
    let [t, q, p, ts, m, s] = slots(&o, [b"t", b"q", b"p", b"T", b"m", b"s"])?;
    if s.str()? != sym {
        return None;
    }
    let id = parse_i64(t.str()?)?;
    let ts = ts.int()?;
    let q = q.str()?;
    out.rec(&Rec::Trade(Trade {
        sym,
        e: ts,
        t: ts,
        a: id,
        id,
        p: p.str()?,
        q,
        f: id,
        l: id,
        m: m.boolean()?,
        nq: q,
        x: b"",
        i: b"",
    }));
    Some(())
}

/// Время биржи кадра, мс: `E` у `bookTicker`/`markPrice`, `T` первой сделки пачки (для лага и свежести сырых кадров).
fn exch_ms(frame: &[u8]) -> Option<i64> {
    let data = parse_object(frame)?.get(b"data")?.raw()?;
    if data.first() == Some(&b'[') {
        let mut ts = None;
        each_elem(data, |e| {
            if ts.is_none() {
                ts = pick(e, [b"T"]).and_then(|[t]| t?.int());
            }
            true
        })?;
        ts
    } else {
        pick(data, [b"E"]).and_then(|[e]| e?.int())
    }
}

impl Exchange for Bingx {
    fn name(&self) -> &'static str {
        "bingx"
    }

    fn kinds(&self) -> &'static [&'static str] {
        &["book", "trade", "mark"]
    }

    /// Тем на соединение — максимум биржи: у всех видов одна тема на символ; назначаемое (по документации 200), потолок меньшего числа ищет лестница.
    fn per_conn(&self, _kind: &str) -> usize {
        MAX_TOPICS
    }

    fn plan(&self, pool: &[String], kinds: &[String], per_conn: usize) -> Vec<ShardPlan> {
        let mut out = Vec::new();
        for kind in kinds {
            let suffix = match kind.as_str() {
                "book" => "bookTicker",
                "trade" => "trade",
                "mark" => "markPrice",
                _ => continue,
            };
            let per = if per_conn == 0 {
                self.per_conn(kind)
            } else {
                per_conn.min(MAX_TOPICS)
            };
            for (i, chunk) in balanced_chunks(pool, per).into_iter().enumerate() {
                out.push(ShardPlan {
                    name: format!("{kind}{i:02}"),
                    endpoint: "swap".into(),
                    streams: chunk.iter().map(|s| format!("{s}@{suffix}")).collect(),
                });
            }
        }
        out
    }

    fn url(&self, _endpoint: &str, host: &str) -> String {
        format!("wss://{host}{PATH}")
    }

    /// Один хост за CDN (независимой точки входа у BingX нет, подготовка п. 5): обе линии — IPv4, запасной вход — IPv6. Лестница 13.11 (`docs/findings/bingx-collector-2026-11-13.md`, п. 4):
    /// все стоянки живой линии (5 ретрансмитов TCP, 10–13 с без данных, затем `ping_degraded`) пришли на IPv6-линии одновременно у нескольких шардов (путь, не биржа); на IPv4 — ни одной.
    /// Первая версия адаптера (линия 1 — IPv6, как у OKX) оставлена в истории ветки.
    fn default_entrances(&self, lanes: usize) -> Vec<Vec<Entrance>> {
        (0..lanes)
            .map(|_| {
                vec![
                    Entrance::new(HOST, Family::V4),
                    Entrance::new(HOST, Family::V6),
                ]
            })
            .collect()
    }

    /// Подписка по одной теме в сообщении: `{"id":…,"reqType":"sub","dataType":"BTC-USDT@bookTicker"}`; id = тема.
    fn subscribe_messages(&self, streams: &[String]) -> Vec<String> {
        streams
            .iter()
            .map(|s| json!({"id": s, "reqType": "sub", "dataType": s}).to_string())
            .collect()
    }

    /// Пауза между сообщениями подписки: 20 мс (≈ 50 сообщений/с проверено в пробе 12.11: 179 тем за 3,6 с без ошибок); лимита сообщений в документации нет.
    fn sub_period(&self) -> Duration {
        Duration::from_millis(20)
    }

    fn new_parser(&self) -> Box<dyn Parser> {
        Box::new(BingxParser)
    }

    fn lag_ms(&self, frame: &[u8], rx_ns: i64) -> Option<f64> {
        Some(lag_ms(rx_ns, exch_ms(frame)?))
    }

    /// Символ из `dataType` кадра данных (`BTC-USDT@bookTicker` → `BTC-USDT`); ответ на подписку (`dataType` пуст) — символа нет.
    fn symbol<'a>(&self, frame: &'a [u8]) -> Option<&'a [u8]> {
        let rest = frame.strip_prefix(b"{\"code\":0,\"dataType\":\"")?;
        let at = rest.iter().position(|&c| c == b'@' || c == b'"')?;
        if rest.get(at) == Some(&b'@') {
            rest.get(..at)
        } else {
            None
        }
    }

    fn load_pool(
        &self,
        min_qv: f64,
        top_exclude: usize,
    ) -> Result<(Vec<String>, Vec<String>), String> {
        let contracts = rest_data(&format!("{REST}/contracts"))?;
        let tickers = rest_data(&format!("{REST}/ticker"))?;
        let (pool, top, extra) = select_pool(&contracts, &tickers, min_qv, top_exclude);
        *self
            .extra
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(extra);
        Ok((pool, top))
    }

    /// Правило G-022 для BingX: весь пул, топ-10 не исключается (правило «без топ-10» — только Binance и Bybit).
    fn default_top_exclude(&self) -> usize {
        0
    }

    fn pool_meta(&self) -> Option<Value> {
        self.extra
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    /// Все кадры BingX — двоичные gzip (данные, ответы на подписку, `Ping`).
    fn gzip_frames(&self) -> bool {
        true
    }

    /// Сервер шлёт текст `Ping` раз в 5 с; без ответа текстом `Pong` соединение закрывается на ≈ 31-й секунде без кадра закрытия (замер W5).
    fn heartbeat_reply(&self, frame: &[u8]) -> Option<&'static str> {
        frame.eq_ignore_ascii_case(b"ping").then_some("Pong")
    }
}

fn rest_data(url: &str) -> Result<Vec<Value>, String> {
    let v = http_get_json_ua(url, Some(UA))?;
    if v.get("code").and_then(Value::as_i64) != Some(0) {
        return Err(format!("{url}: code {:?}", v.get("code")));
    }
    Ok(v.get("data")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default())
}

fn text<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k)?.as_str()
}

/// Число из JSON-значения: число или строка с числом (REST BingX отдаёт оборот строкой).
fn number(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str()?.parse().ok())
}

/// `contracts` — `/quote/contracts`, `tickers` — `/quote/ticker`. Контракты `currency=USDT`, `status=1` («online»; 25 — запрещено открывать позиции, исключены как
/// не торгуемые полностью); оборот за сутки — `quoteVolume` тикера (в USDT). Возвращает (пул, исключённый топ, `{meta, qv}` пула: параметры контракта и оборот для
/// читателя и ранжирования ступеней).
pub fn select_pool(
    contracts: &[Value],
    tickers: &[Value],
    min_qv: f64,
    top_exclude: usize,
) -> (Vec<String>, Vec<String>, Value) {
    let turnover: BTreeMap<&str, f64> = tickers
        .iter()
        .filter_map(|t| Some((text(t, "symbol")?, number(t.get("quoteVolume")?)?)))
        .filter(|(_, v)| *v >= 0.0)
        .collect();
    let live: Vec<&Value> = contracts
        .iter()
        .filter(|c| {
            text(c, "currency") == Some("USDT")
                && c.get("status").and_then(Value::as_i64) == Some(1)
        })
        .collect();
    let qv: BTreeMap<String, f64> = live
        .iter()
        .filter_map(|c| {
            let sym = text(c, "symbol")?;
            Some((sym.to_string(), *turnover.get(sym)?))
        })
        .collect();
    let (pool, top) = rank_pool(&qv, min_qv, top_exclude);
    let mut meta = Map::new();
    let mut vols = Map::new();
    for c in &live {
        let Some(sym) = text(c, "symbol").filter(|s| pool.iter().any(|p| p == s)) else {
            continue;
        };
        let m: Map<String, Value> = META_KEYS
            .iter()
            .filter_map(|k| Some(((*k).to_string(), c.get(*k)?.clone())))
            .collect();
        meta.insert(sym.to_string(), Value::Object(m));
        if let Some(v) = qv.get(sym) {
            vols.insert(sym.to_string(), json!((v * 100.0).round() / 100.0));
        }
    }
    (pool, top, json!({"meta": meta, "qv": vols}))
}
