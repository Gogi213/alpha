//! Адаптер Binance USD-M для ядра коллектора: адреса, подписка, пул, разбор кадра.
//!
//! Виды: bookTicker (эндпоинт `/public`), aggTrade и markPrice `!markPrice@arr@1s` (эндпоинт `/market`); L1 + сделки +
//! mark/фандинг, без глубины (В-115). Разбор строгий: набор ключей объекта точно как ожидается, иначе кадр пишется как пришёл.

use std::collections::BTreeMap;
use std::time::Duration;

use serde_json::json;

use super::codec::{Book, Mark, Rec, Trade};
use super::exchange::{lag_ms, Entrance, Exchange, Family, Out, Parsed, Parser, ShardPlan};
use super::pool::{balanced_chunks, http_get_json, rank_pool};
use super::scan::{each_elem, parse_object, Obj, Val};

const REST: &str = "https://fapi.binance.com";
const HOST: &str = "fstream.binance.com";
const SUB_BATCH: usize = 100;
const MARK_STREAM: &str = "!markPrice@arr@1s";

pub struct Binance;

/// Ключи объекта: каждый ровно раз, все `N` — иначе `None`. Значения — в слотах по порядку `names`.
fn slots<'a, const N: usize>(o: &Obj<'a>, names: [&[u8]; N]) -> Option<[Val<'a>; N]> {
    if o.len() != N {
        return None;
    }
    let mut out: [Option<Val<'a>>; N] = [None; N];
    for f in o.iter() {
        let i = names.iter().position(|n| *n == f.key)?;
        if out.get_mut(i)?.replace(f.val).is_some() {
            return None;
        }
    }
    let mut res = [Val::Null; N];
    for (r, v) in res.iter_mut().zip(out) {
        *r = v?;
    }
    Some(res)
}

fn is_one(v: Val<'_>) -> bool {
    v.int() == Some(1)
}

fn book(data: &[u8]) -> Option<Book<'_>> {
    let o = parse_object(data)?;
    let [e, u, s, ps, b, bq, a, aq, t, ev, st] = slots(
        &o,
        [
            b"e", b"u", b"s", b"ps", b"b", b"B", b"a", b"A", b"T", b"E", b"st",
        ],
    )?;
    let sym = s.str()?;
    if e.str()? != b"bookTicker" || ps.str()? != sym || !is_one(st) {
        return None;
    }
    Some(Book {
        sym,
        e: ev.int()?,
        t: t.int()?,
        u: u.int()?,
        id: u.int()?,
        bid: b.str()?,
        bsz: bq.str()?,
        ask: a.str()?,
        asz: aq.str()?,
    })
}

fn trade(data: &[u8]) -> Option<Trade<'_>> {
    let o = parse_object(data)?;
    let [e, ev, a, s, p, q, nq, f, l, t, m, st] = slots(
        &o,
        [
            b"e", b"E", b"a", b"s", b"p", b"q", b"nq", b"f", b"l", b"T", b"m", b"st",
        ],
    )?;
    if e.str()? != b"aggTrade" || !is_one(st) {
        return None;
    }
    Some(Trade {
        sym: s.str()?,
        e: ev.int()?,
        t: t.int()?,
        a: a.int()?,
        id: a.int()?,
        p: p.str()?,
        q: q.str()?,
        f: f.int()?,
        l: l.int()?,
        m: m.boolean()?,
        nq: nq.str()?,
        x: b"",
        i: b"",
    })
}

fn mark(elem: &[u8]) -> Option<Mark<'_>> {
    let o = parse_object(elem)?;
    let [e, ev, s, p, ap, pp, i, r, t, st] = slots(
        &o,
        [b"e", b"E", b"s", b"p", b"ap", b"P", b"i", b"r", b"T", b"st"],
    )?;
    if e.str()? != b"markPriceUpdate" || !is_one(st) {
        return None;
    }
    Some(Mark {
        sym: s.str()?,
        e: ev.int()?,
        p: p.str()?,
        ap: ap.str()?,
        pp: pp.str()?,
        i: i.str()?,
        r: r.str()?,
        t: t.int()?,
    })
}

struct BinanceParser;

impl Parser for BinanceParser {
    fn parse(&mut self, frame: &[u8], out: &mut dyn Out) -> Parsed {
        parse_frame(frame, out).map_or(Parsed::Raw, Parsed::Recs)
    }
}

/// `{"stream":"<имя>","data":<json>}` без разбора вложенного значения: имя потока и срез `data`. Другая форма (ответ подписки, лишние ключи) — `None`:
/// тогда обычный разбор верхнего объекта.
fn envelope(frame: &[u8]) -> Option<(&[u8], &[u8])> {
    const MID: &[u8] = b"\",\"data\":";
    let rest = frame.strip_prefix(b"{\"stream\":\"")?;
    let at = rest.windows(MID.len()).position(|w| w == MID)?;
    let stream = rest.get(..at)?;
    let data = rest.get(at + MID.len()..)?.strip_suffix(b"}")?;
    (!stream.contains(&b'"')).then_some((stream, data))
}

fn parse_frame(frame: &[u8], out: &mut dyn Out) -> Option<usize> {
    let (stream, data) = match envelope(frame) {
        Some((s, d)) => (Some(s), d),
        None => {
            let top = parse_object(frame)?;
            (
                top.get(b"stream").and_then(|v| v.str()),
                top.get(b"data")?.raw()?,
            )
        }
    };
    match data.first()? {
        b'{' => {
            // тип события — по набору ключей: у книги и сделки они разные
            if let Some(b) = book(data) {
                out.rec(&Rec::Book(b));
            } else {
                out.rec(&Rec::Trade(trade(data)?));
            }
            Some(1)
        }
        b'[' => {
            // массив mark поэлементно: USDⓈ-M (`st` = 1) — записи; прочие (COIN-M `st` = 2, новые поля) — один `raw` в кадре той же формы.
            // Прежняя схема «целиком или никак» писала сырым весь кадр на ≈ 750 элементов из-за ≈ 30 элементов с `st` = 2 (замер 12.11).
            let n = each_elem(data, |_| true)?;
            let mut good = 0usize;
            each_elem(data, |e| {
                good += usize::from(mark(e).is_some());
                true
            })?;
            if n == 0 || good == 0 {
                return None;
            }
            let mut bad: Vec<u8> = Vec::new();
            each_elem(data, |e| {
                match mark(e) {
                    Some(m) => out.rec(&Rec::Mark(m)),
                    None => {
                        bad.push(if bad.is_empty() { b'[' } else { b',' });
                        bad.extend_from_slice(e);
                    }
                }
                true
            })?;
            if !bad.is_empty() {
                bad.push(b']');
                out.raw(&wrap(stream, &bad));
            }
            Some(good)
        }
        _ => None,
    }
}

/// Кадр той же формы, что исходный (`{"stream":…,"data":[…]}`), но только с неразобранными элементами.
fn wrap(stream: Option<&[u8]>, elems: &[u8]) -> Vec<u8> {
    let mut w = Vec::with_capacity(elems.len() + 64);
    w.push(b'{');
    if let Some(s) = stream {
        w.extend_from_slice(b"\"stream\":\"");
        w.extend_from_slice(s);
        w.extend_from_slice(b"\",");
    }
    w.extend_from_slice(b"\"data\":");
    w.extend_from_slice(elems);
    w.push(b'}');
    w
}

impl Exchange for Binance {
    fn name(&self) -> &'static str {
        "binance"
    }

    fn kinds(&self) -> &'static [&'static str] {
        &["book", "trade", "mark"]
    }

    fn per_conn(&self, kind: &str) -> usize {
        match kind {
            "book" => 32,
            "trade" => 64,
            _ => 1,
        }
    }

    fn plan(&self, pool: &[String], kinds: &[String], per_conn: usize) -> Vec<ShardPlan> {
        let mut out = Vec::new();
        for kind in kinds {
            if kind == "mark" {
                out.push(ShardPlan {
                    name: "mark00".into(),
                    endpoint: "market".into(),
                    streams: vec![MARK_STREAM.into()],
                });
                continue;
            }
            let (suffix, endpoint) = match kind.as_str() {
                "book" => ("bookTicker", "public"),
                "trade" => ("aggTrade", "market"),
                _ => continue,
            };
            let per = if per_conn == 0 {
                self.per_conn(kind)
            } else {
                per_conn
            };
            let streams: Vec<String> = pool
                .iter()
                .map(|s| format!("{}@{suffix}", s.to_lowercase()))
                .collect();
            for (i, chunk) in balanced_chunks(&streams, per).into_iter().enumerate() {
                out.push(ShardPlan {
                    name: format!("{kind}{i:02}"),
                    endpoint: endpoint.into(),
                    streams: chunk,
                });
            }
        }
        out
    }

    fn url(&self, endpoint: &str, host: &str) -> String {
        format!("wss://{host}/{endpoint}/stream")
    }

    fn default_entrances(&self, lanes: usize) -> Vec<Vec<Entrance>> {
        (0..lanes)
            .map(|_| vec![Entrance::new(HOST, Family::Any)])
            .collect()
    }

    fn subscribe_messages(&self, streams: &[String]) -> Vec<String> {
        streams
            .chunks(SUB_BATCH)
            .enumerate()
            .map(|(k, c)| {
                json!({"method": "SUBSCRIBE", "params": c, "id": k * SUB_BATCH + 1}).to_string()
            })
            .collect()
    }

    fn sub_period(&self) -> Duration {
        Duration::from_millis(200)
    }

    fn new_parser(&self) -> Box<dyn Parser> {
        Box::new(BinanceParser)
    }

    fn lag_ms(&self, frame: &[u8], rx_ns: i64) -> Option<f64> {
        let data = parse_object(frame)?.get(b"data")?.raw()?;
        let e = match data.first()? {
            b'{' => parse_object(data)?.get(b"E")?.int()?,
            b'[' => {
                let mut first: Option<i64> = None;
                each_elem(data, |el| {
                    first = parse_object(el)
                        .and_then(|o| o.get(b"E"))
                        .and_then(Val::int);
                    false
                });
                first?
            }
            _ => return None,
        };
        Some(lag_ms(rx_ns, e))
    }

    fn symbol<'a>(&self, frame: &'a [u8]) -> Option<&'a [u8]> {
        let rest = frame.strip_prefix(b"{\"stream\":\"")?;
        if rest.first() == Some(&b'!') {
            return None;
        }
        let end = rest.iter().position(|&c| c == b'@')?;
        rest.get(..end)
    }

    fn load_pool(
        &self,
        min_qv: f64,
        top_exclude: usize,
    ) -> Result<(Vec<String>, Vec<String>), String> {
        let info = http_get_json(&format!("{REST}/fapi/v1/exchangeInfo"))?;
        let tickers = http_get_json(&format!("{REST}/fapi/v1/ticker/24hr"))?;
        Ok(select_pool(&info, &tickers, min_qv, top_exclude))
    }
}

/// `info` — ответ exchangeInfo, `tickers` — ticker/24hr: торгуемые бессрочные USDT-контракты с оборотом.
pub fn select_pool(
    info: &serde_json::Value,
    tickers: &serde_json::Value,
    min_qv: f64,
    top_exclude: usize,
) -> (Vec<String>, Vec<String>) {
    let mut qv: BTreeMap<String, f64> = BTreeMap::new();
    for t in tickers.as_array().into_iter().flatten() {
        let v = t
            .get("quoteVolume")
            .and_then(|x| x.as_str())
            .and_then(|x| x.parse::<f64>().ok());
        if let (Some(sym), Some(v)) = (t.get("symbol").and_then(|x| x.as_str()), v) {
            if v >= 0.0 {
                qv.insert(sym.to_string(), v);
            }
        }
    }
    let ok: BTreeMap<String, f64> = info
        .get("symbols")
        .and_then(|x| x.as_array())
        .into_iter()
        .flatten()
        .filter(|s| {
            s.get("status").and_then(|x| x.as_str()) == Some("TRADING")
                && s.get("contractType").and_then(|x| x.as_str()) == Some("PERPETUAL")
                && s.get("quoteAsset").and_then(|x| x.as_str()) == Some("USDT")
        })
        .filter_map(|s| {
            let name = s.get("symbol")?.as_str()?;
            Some((name.to_string(), *qv.get(name)?))
        })
        .collect();
    rank_pool(&ok, min_qv, top_exclude)
}
