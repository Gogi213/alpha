//! Адаптер Gate public WebSocket v4 (USDT-M фьючерсы) для ядра коллектора: адреса, подписка, пул, разбор кадра.
//!
//! Виды (`docs/findings/gate-ws-prep-2026-11-15.md`): book — `futures.book_ticker` (верх книги, склейка биржей по таймеру ≈ 10 мс на соединение), trade —
//! `futures.trades` (одна сделка в кадре, размер со знаком: плюс — тейкер покупает), mark — `futures.tickers` (отдельного канала mark/index/funding у Gate нет:
//! тикер несёт `mark_price`, `index_price`, `funding_rate`, `funding_next_apply`; остальные поля тикера — не mark и в формат не входят).
//! Один эндпоинт `/v4/ws/usdt` на все виды; подписка по одному контракту на сообщение (один неизвестный контракт проваливает всё сообщение);
//! кадр — `{"time","time_ms","channel","event":"update","result":…}`. Заголовок рукопожатия `X-Gate-Size-Decimal: 1` обязателен: без него биржа шлёт размеры числом,
//! усечённым до целого (`0.8` → `0`).
//! Разбор строгий: набор ключей кадра, книги и сделки точно как в замерах 15.11, иначе элемент (или весь кадр) пишется как пришёл; у тикера (22–23 поля, больше
//! `MAX_FIELDS` сканера) берутся шесть нужных ключей, остальные пропускаются.
//! Личность записи (снятие дублей между линиями и заменами): book — `u` (общий между соединениями), trade — `id` (подряд по контракту), mark — `t`.

use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use super::codec::{Book, Mark, Rec, Trade};
use super::exchange::{
    lag_ms, Entrance, Exchange, Family, Out, Parsed, Parser, ShardPlan, TradeSeq, Turnover,
};
use super::pool::{balanced_chunks, http_get_json_ua, rank_pool};
use super::scan::{each_elem, parse_object, pick, slots, Val};

const REST: &str = "https://api.gateio.ws/api/v4/futures/usdt";
const HOST: &str = "fx-ws.gateio.ws";
const PATH: &str = "/v4/ws/usdt";
const UA: &str = "alpha-collector/gate";
/// Контрактов на соединение: назначаемое. Лимита подписок у Gate нет (в документации — ни числа, ни слова; замер 15.11: 1 051 контракт × 3 канала на одном
/// соединении подписались без ошибок), потолок ищет лестница.
pub const PER_CONN: usize = 100;
const META_KEYS: [&str; 13] = [
    "quanto_multiplier",
    "order_size_min",
    "order_price_round",
    "mark_price_round",
    "enable_decimal",
    "contract_type",
    "funding_interval",
    "leverage_max",
    "is_pre_market",
    "in_delisting",
    "mark_type",
    "type",
    "launch_time",
];

#[derive(Default)]
pub struct Gate {
    /// `meta` и `qv` пула (из последнего `load_pool`) — рядом с `pool-<день>.json`.
    extra: Mutex<Option<Value>>,
}

/// Парсер без состояния: записи кадра не зависят от предыдущих.
struct GateParser;

impl Parser for GateParser {
    fn parse(&mut self, frame: &[u8], out: &mut dyn Out) -> Parsed {
        parse_frame(frame, out).map_or(Parsed::Raw, Parsed::Recs)
    }
}

/// Кадр той же формы, что исходный, но только с неразобранными элементами `result`.
fn wrap(time: &[u8], time_ms: &[u8], ch: &[u8], elems: &[u8]) -> Vec<u8> {
    let mut w = Vec::with_capacity(elems.len() + 96);
    w.extend_from_slice(b"{\"time\":");
    w.extend_from_slice(time);
    w.extend_from_slice(b",\"time_ms\":");
    w.extend_from_slice(time_ms);
    w.extend_from_slice(b",\"channel\":\"");
    w.extend_from_slice(ch);
    w.extend_from_slice(b"\",\"event\":\"update\",\"result\":");
    w.extend_from_slice(elems);
    w.push(b'}');
    w
}

fn parse_frame(frame: &[u8], out: &mut dyn Out) -> Option<usize> {
    let top = parse_object(frame)?;
    let [time, time_ms, ch, ev, result] =
        slots(&top, [b"time", b"time_ms", b"channel", b"event", b"result"])?;
    if ev.str()? != b"update" {
        return None;
    }
    let (time, time_ms) = (num_text(time)?, num_text(time_ms)?);
    let (ch, result) = (ch.str()?, result.raw()?);
    match ch {
        b"futures.book_ticker" => book(result, out),
        b"futures.trades" => elems(time, time_ms, ch, result, out, trade),
        b"futures.tickers" => elems(time, time_ms, ch, result, out, tick),
        _ => None,
    }
}

/// Число кадра как написано (целое без знака у `time`/`time_ms`).
fn num_text<'a>(v: Val<'a>) -> Option<&'a [u8]> {
    match v {
        Val::Num(s) if v.int().is_some() => Some(s),
        _ => None,
    }
}

/// Массив `result` поэлементно: годные элементы — записи, негодные — один `raw` в кадре той же формы; ничего не теряется и не дублируется. Ни одного годного —
/// `None` (кадр целиком сырым, `out` пуст).
fn elems<'a>(
    time: &[u8],
    time_ms: &[u8],
    ch: &[u8],
    result: &'a [u8],
    out: &mut dyn Out,
    one: impl Fn(&'a [u8], &mut dyn Out) -> Option<()>,
) -> Option<usize> {
    if each_elem(result, |_| true)? == 0 {
        return None;
    }
    let (mut good, mut bad): (usize, Vec<u8>) = (0, Vec::new());
    each_elem(result, |e| {
        if one(e, out).is_some() {
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
        out.raw(&wrap(time, time_ms, ch, &bad));
    }
    Some(good)
}

/// `{"t":…,"u":…,"s":…,"b":…,"B":…,"a":…,"A":…}`: у Gate одно время (`t`, мс) — E = T; `u` — номер обновления книги контракта, общий между соединениями
/// (замер 15.11: значения на общих `u` совпали в 91 251 из 91 251). Пустая сторона (`b` или `a` = `""`) — как пришла.
fn book(result: &[u8], out: &mut dyn Out) -> Option<usize> {
    let o = parse_object(result)?;
    let [t, u, s, bid, bsz, ask, asz] = slots(&o, [b"t", b"u", b"s", b"b", b"B", b"a", b"A"])?;
    let (t, u) = (t.int()?, u.int()?);
    out.rec(&Rec::Book(Book {
        sym: s.str()?,
        e: t,
        t,
        u,
        id: u,
        bid: bid.str()?,
        bsz: bsz.str()?,
        ask: ask.str()?,
        asz: asz.str()?,
        bn: b"",
        an: b"",
    }));
    Some(1)
}

/// `{"id":…,"size":"-1","create_time":…,"create_time_ms":…,"price":"…","contract":"…"[,"is_internal":true]}`: размер со знаком — плюс тейкер покупает, минус продаёт
/// (запись: `q` без знака, `m` = тейкер продаёт); `is_internal` (поглощение ликвидаций страховым фондом и ADL; поле опускается у обычной сделки) — признак `I`
/// в столбце `x`. Размер числом (заголовок `X-Gate-Size-Decimal` не принят) — элемент сырым: усечённый размер в файл не идёт.
fn trade(e: &[u8], out: &mut dyn Out) -> Option<()> {
    let o = parse_object(e)?;
    let (id, size, ctime, ms, price, contract, internal) = if o.len() == 7 {
        let [id, size, ctime, ms, price, contract, internal] = slots(
            &o,
            [
                b"id",
                b"size",
                b"create_time",
                b"create_time_ms",
                b"price",
                b"contract",
                b"is_internal",
            ],
        )?;
        (id, size, ctime, ms, price, contract, internal.boolean()?)
    } else {
        let [id, size, ctime, ms, price, contract] = slots(
            &o,
            [
                b"id",
                b"size",
                b"create_time",
                b"create_time_ms",
                b"price",
                b"contract",
            ],
        )?;
        (id, size, ctime, ms, price, contract, false)
    };
    ctime.int()?;
    let (id, ms) = (id.int()?, ms.int()?);
    let size = size.str()?;
    let sell = size.first() == Some(&b'-');
    let q = if sell { size.get(1..)? } else { size };
    if q.is_empty() {
        return None;
    }
    out.rec(&Rec::Trade(Trade {
        sym: contract.str()?,
        e: ms,
        t: ms,
        a: id,
        id,
        p: price.str()?,
        q,
        f: id,
        l: id,
        m: sell,
        nq: q,
        x: if internal { b"I" } else { b"" },
        i: b"",
    }));
    Some(())
}

/// Элемент `futures.tickers` (22 поля, у первого кадра после подписки 23): нужны `contract`, `mark_price`, `index_price`, `funding_rate`, `funding_next_apply`
/// (секунды — в записи миллисекунды, как у остальных бирж: время начисления), `t` (мс; снимок на ≈ 456 мс старше кадра). Тикер идёт раз в ≈ 1 с на контракт и
/// только при изменении; остальные поля (`last`, объёмы, high/low, `change_*`) — не mark, в формат не входят (решение координатора: mark как шлёт биржа).
fn tick(e: &[u8], out: &mut dyn Out) -> Option<()> {
    let [contract, mark, index, rate, next, t] = pick(
        e,
        [
            b"contract",
            b"mark_price",
            b"index_price",
            b"funding_rate",
            b"funding_next_apply",
            b"t",
        ],
    )?;
    out.rec(&Rec::Mark(Mark {
        sym: contract?.str()?,
        e: t?.int()?,
        p: mark?.str()?,
        ap: b"",
        pp: b"",
        i: index?.str()?,
        r: rate?.str()?,
        t: next?.int()?.checked_mul(1000)?,
    }));
    Some(())
}

/// Время биржи кадра, мс, для лага пути и свежести сырых кадров: `t` книги, `create_time_ms` первой сделки; у тикера — `time_ms` кадра (отправка шлюзом): его `t` — время
/// снимка, на ≈ 456 мс старше кадра (замер 15.11), и лаг по `t` (p50 ≈ 580 мс) отбраковывал бы каждый запасной путь шарда mark (проверка пути: p50 ≤ 350 мс — лестница 15.11,
/// окна n80-w1/w2: 10 отказов подряд у `mark00`). Лаг записи `m` в файле — по её `E` = `t`, как шлёт биржа.
fn exch_ms(frame: &[u8]) -> Option<i64> {
    let [result, time_ms, channel] = pick(frame, [b"result", b"time_ms", b"channel"])?;
    let r = result?.raw()?;
    if channel?.str()? == b"futures.tickers" {
        return time_ms?.int();
    }
    if r.first() == Some(&b'[') {
        let mut ts = None;
        each_elem(r, |e| {
            if ts.is_none() {
                ts = pick(e, [b"t", b"create_time_ms"]).and_then(|[t, c]| t.or(c)?.int());
            }
            true
        })?;
        ts
    } else {
        pick(r, [b"t"]).and_then(|[t]| t?.int())
    }
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

impl Exchange for Gate {
    /// Номер сделки Gate сплошной по контракту (проверка 16.11: 0,9 млн записей, 6 подтверждённых пропусков / 8 номеров, все у AKE_USDT).
    fn trade_seq(&self) -> Option<TradeSeq> {
        Some(TradeSeq::A)
    }

    fn name(&self) -> &'static str {
        "gate"
    }

    fn kinds(&self) -> &'static [&'static str] {
        &["book", "trade", "mark"]
    }

    fn per_conn(&self, _kind: &str) -> usize {
        PER_CONN
    }

    fn plan(&self, pool: &[String], kinds: &[String], per_conn: usize) -> Vec<ShardPlan> {
        let mut out = Vec::new();
        for kind in kinds {
            let channel = match kind.as_str() {
                "book" => "futures.book_ticker",
                "trade" => "futures.trades",
                "mark" => "futures.tickers",
                _ => continue,
            };
            let per = if per_conn == 0 {
                self.per_conn(kind)
            } else {
                per_conn
            };
            for (i, chunk) in balanced_chunks(pool, per).into_iter().enumerate() {
                out.push(ShardPlan {
                    name: format!("{kind}{i:02}"),
                    endpoint: "usdt".into(),
                    streams: chunk.iter().map(|s| format!("{channel}:{s}")).collect(),
                });
            }
        }
        out
    }

    fn url(&self, _endpoint: &str, host: &str) -> String {
        format!("wss://{host}{PATH}")
    }

    /// Один хост `fx-ws.gateio.ws` (AWS Токио, 13 адресов IPv4 по кругу DNS, IPv6 нет; подготовка п. 4): обе линии — IPv4, запасного входа нет.
    fn default_entrances(&self, lanes: usize) -> Vec<Vec<Entrance>> {
        (0..lanes)
            .map(|_| vec![Entrance::new(HOST, Family::V4)])
            .collect()
    }

    /// Подписка по одному контракту в сообщении: `{"time":<с>,"id":<n>,"channel":"futures.book_ticker","event":"subscribe","payload":["BTC_USDT"]}`;
    /// один неизвестный контракт проваливает всё сообщение, поэтому — по одному (замер 15.11).
    fn subscribe_messages(&self, streams: &[String]) -> Vec<String> {
        event_messages("subscribe", streams)
    }

    fn unsubscribe_messages(&self, streams: &[String]) -> Vec<String> {
        event_messages("unsubscribe", streams)
    }

    /// Пауза между сообщениями подписки: 1 мс — назначаемое; лимита сообщений у Gate нет (в документации «Others: No limit», замер 15.11: 1 051 сообщение подряд без пауз —
    /// 1 051 × `success`).
    fn sub_period(&self) -> Duration {
        Duration::from_millis(1)
    }

    fn new_parser(&self) -> Box<dyn Parser> {
        Box::new(GateParser)
    }

    fn lag_ms(&self, frame: &[u8], rx_ns: i64) -> Option<f64> {
        Some(lag_ms(rx_ns, exch_ms(frame)?))
    }

    /// Контракт кадра данных: `s` книги, `contract` первого элемента сделок и тикеров; ответ на подписку, `futures.pong`, системные кадры — символа нет.
    fn symbol<'a>(&self, frame: &'a [u8]) -> Option<&'a [u8]> {
        let r = frame.get(find(frame, b"\"result\":")? + 9..)?;
        let key: &[u8] = match r.first()? {
            b'{' => b"\"s\":\"",
            b'[' => b"\"contract\":\"",
            _ => return None,
        };
        let rest = r.get(find(r, key)? + key.len()..)?;
        rest.get(..rest.iter().position(|&c| c == b'"')?)
    }

    fn load_pool(
        &self,
        min_qv: f64,
        top_exclude: usize,
    ) -> Result<(Vec<String>, Vec<String>), String> {
        let contracts = rest_array(&format!("{REST}/contracts"))?;
        let tickers = rest_array(&format!("{REST}/tickers"))?;
        let (pool, top, extra) = select_pool(&contracts, &tickers, min_qv, top_exclude);
        *self
            .extra
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(extra);
        Ok((pool, top))
    }

    fn load_turnover(&self) -> Result<Turnover, String> {
        let contracts = rest_array(&format!("{REST}/contracts"))?;
        let tickers = rest_array(&format!("{REST}/tickers"))?;
        let (live, qv) = universe(&contracts, &tickers);
        let (meta, _) = meta_vols(&live, &qv, &|_| true);
        Ok(Turnover {
            qv,
            meta: meta.into_iter().collect(),
        })
    }

    fn pool_meta(&self) -> Option<Value> {
        self.extra
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    /// `{"channel":"futures.system","event":"update","result":{"type":"upgrade",…}}` — перед обновлением сервиса: открыть замену, старое закрыть после перекрытия.
    fn is_retire_notice(&self, frame: &[u8]) -> bool {
        find(frame, b"\"channel\":\"futures.system\"").is_some()
            && find(frame, b"\"type\":\"upgrade\"").is_some()
    }

    /// Без этого заголовка биржа шлёт `size` числом, усечённым до целого (ETH `0.8` → `0`), у ≈ 30 % сделок ETH дробная часть теряется (замер 15.11).
    fn ws_headers(&self) -> &'static [(&'static str, &'static str)] {
        &[("X-Gate-Size-Decimal", "1")]
    }
}

fn rest_array(url: &str) -> Result<Vec<Value>, String> {
    let v = http_get_json_ua(url, Some(UA))?;
    v.as_array()
        .cloned()
        .ok_or_else(|| format!("{url}: ответ не массив"))
}

fn text<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k)?.as_str()
}

/// Число из JSON-значения: число или строка с числом (REST Gate отдаёт оборот строкой).
fn number(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str()?.parse().ok())
}

/// По одному контракту в сообщении: `{"time": <с>, "id": n, "channel": …, "event": "subscribe"|"unsubscribe", "payload": ["BTC_USDT"]}`.
fn event_messages(event: &str, streams: &[String]) -> Vec<String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    streams
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            let (ch, sym) = s.split_once(':')?;
            Some(
                json!({"time": now, "id": i + 1, "channel": ch, "event": event, "payload": [sym]})
                    .to_string(),
            )
        })
        .collect()
}

/// `contracts` — `/futures/usdt/contracts`, `tickers` — `/futures/usdt/tickers`. Контракты `status = "trading"`; оборот за сутки в долларах — `volume_24h_quote` тикера
/// (USDT). Возвращает (живые контракты, оборот по имени).
fn universe<'a>(
    contracts: &'a [Value],
    tickers: &[Value],
) -> (Vec<&'a Value>, BTreeMap<String, f64>) {
    let turnover: BTreeMap<&str, f64> = tickers
        .iter()
        .filter_map(|t| Some((text(t, "contract")?, number(t.get("volume_24h_quote")?)?)))
        .filter(|(_, v)| *v >= 0.0)
        .collect();
    let live: Vec<&Value> = contracts
        .iter()
        .filter(|c| text(c, "status") == Some("trading"))
        .collect();
    let qv: BTreeMap<String, f64> = live
        .iter()
        .filter_map(|c| {
            let name = text(c, "name")?;
            Some((name.to_string(), *turnover.get(name)?))
        })
        .collect();
    (live, qv)
}

/// Параметры контракта (`META_KEYS`) и оборот (округлён до цента) контрактов, прошедших `keep`.
fn meta_vols(
    live: &[&Value],
    qv: &BTreeMap<String, f64>,
    keep: &dyn Fn(&str) -> bool,
) -> (Map<String, Value>, Map<String, Value>) {
    let mut meta = Map::new();
    let mut vols = Map::new();
    for c in live {
        let Some(name) = text(c, "name").filter(|s| keep(s)) else {
            continue;
        };
        let m: Map<String, Value> = META_KEYS
            .iter()
            .filter_map(|k| Some(((*k).to_string(), c.get(*k)?.clone())))
            .collect();
        meta.insert(name.to_string(), Value::Object(m));
        if let Some(v) = qv.get(name) {
            vols.insert(name.to_string(), json!((v * 100.0).round() / 100.0));
        }
    }
    (meta, vols)
}

/// `contracts` — `/futures/usdt/contracts`, `tickers` — `/futures/usdt/tickers`. Возвращает (пул, исключённый топ, `{meta, qv}` пула: параметры контракта и оборот для
/// читателя и ранжирования ступеней).
pub fn select_pool(
    contracts: &[Value],
    tickers: &[Value],
    min_qv: f64,
    top_exclude: usize,
) -> (Vec<String>, Vec<String>, Value) {
    let (live, qv) = universe(contracts, tickers);
    let (pool, top) = rank_pool(&qv, min_qv, top_exclude);
    let keep: std::collections::HashSet<&str> = pool.iter().map(String::as_str).collect();
    let (meta, vols) = meta_vols(&live, &qv, &|id| keep.contains(id));
    (pool, top, json!({"meta": meta, "qv": vols}))
}
