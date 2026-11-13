//! Адаптер Bybit v5 public linear (USDT-бессрочные) для ядра коллектора: адреса, подписка, пул, разбор кадра.
//!
//! Виды: orderbook.1 (L1), publicTrade (сделки), tickers (mark/index/фандинг; дельты сливаются в состояние символа).
//! Глубины нет (В-115). Один эндпоинт на все виды. Отличия от Binance (замер TK-532): сделки пачкой на один ордер тейкера
//! с общим `seq` и `T`, id сделки — UUID без порядка (ключ порядка `a = seq·1000 + номер в пачке`); L1 всегда
//! `type=snapshot`, повтор кадра с тем же `u`. Входы: `stream.bybit.com` (IPv4) и `stream.bybit.kz` (TK-532/g022-integrate).

use std::collections::{BTreeMap, HashMap};
use std::time::Duration;

use serde_json::json;

use super::codec::{Book, Mark, Rec, Trade};
use super::exchange::{lag_ms, Entrance, Exchange, Family, Out, Parsed, Parser, ShardPlan};
use super::hash::fnv;
use super::pool::{balanced_chunks, http_get_json, rank_pool};
use super::scan::{each_elem, parse_i64, parse_object, pick, single_pair, slots, Obj, Small};

const REST: &str = "https://api.bybit.com";
const PATH: &str = "/v5/public/linear";
const HOST_COM: &str = "stream.bybit.com";
const HOST_KZ: &str = "stream.bybit.kz";
const HOST_GE: &str = "stream.bybitgeorgia.ge";
const SUB_BATCH: usize = 100;
/// Место сделки в пачке с общим seq; пачка Bybit — до сотен сделок, 1000 с запасом (больше — кадр пишется сырым).
const TRADE_SLOT: i64 = 1000;

pub struct Bybit;

/// Полное состояние mark символа: `tickers` шлёт дельты, запись `m` — всегда полное состояние.
#[derive(Clone, Copy)]
struct MarkState {
    ts: i64,
    mark: Small,
    index: Small,
    funding: Small,
    next: Small,
}

struct BybitParser {
    marks: HashMap<Box<[u8]>, MarkState>,
}

impl MarkState {
    fn slot(&mut self, i: usize) -> &mut Small {
        match i {
            0 => &mut self.mark,
            1 => &mut self.index,
            2 => &mut self.funding,
            _ => &mut self.next,
        }
    }
}

impl Parser for BybitParser {
    fn parse(&mut self, frame: &[u8], out: &mut dyn Out) -> Parsed {
        self.parse_frame(frame, out)
            .map_or(Parsed::Raw, Parsed::Recs)
    }
}

impl BybitParser {
    fn parse_frame(&mut self, frame: &[u8], out: &mut dyn Out) -> Option<usize> {
        let top = parse_object(frame)?;
        let topic = top.get(b"topic")?.str()?;
        let kind = topic.split(|&c| c == b'.').next()?;
        match kind {
            b"orderbook" => {
                let b = Self::book_of(&top)?;
                out.rec(&Rec::Book(b));
                Some(1)
            }
            b"publicTrade" => Self::trades(&top, out),
            b"tickers" => self.tickers(&top, out),
            _ => None,
        }
    }

    fn book_of<'a>(top: &Obj<'a>) -> Option<Book<'a>> {
        let [_, ts, ty, data, cts] = slots(top, [b"topic", b"ts", b"type", b"data", b"cts"])?;
        if ty.str()? != b"snapshot" {
            return None;
        }
        let x = parse_object(data.raw()?)?;
        let [s, b, a, u, seq] = slots(&x, [b"s", b"b", b"a", b"u", b"seq"])?;
        let (bid, bsz) = single_pair(b.raw()?)?;
        let (ask, asz) = single_pair(a.raw()?)?;
        // `u` Bybit у каждого входа (com, kz, …) считается своим счётчиком: одно и то же состояние книги приходит с разными `u`
        // и одинаковым сквозным `seq` (замер 12.11, захват кадров двух линий) — в файл и в личность идёт `seq`; `u` только проверяется
        u.int()?;
        Some(Book {
            sym: s.str()?,
            e: ts.int()?,
            t: cts.int()?,
            u: seq.int()?,
            id: seq.int()?,
            bid,
            bsz,
            ask,
            asz,
            bn: b"",
            an: b"",
        })
    }

    /// Одна сделка пачки: ключи ровно как ожидается, `BT`/`RPI` — булевы (в столбец `x`), сторона Buy/Sell.
    fn fill<'a>(elem: &'a [u8], ts: i64, seq_idx: i64) -> Option<Trade<'a>> {
        let o = parse_object(elem)?;
        let [t, s, side, v, p, _l, uuid, bt, rpi, seq] = slots(
            &o,
            [
                b"T", b"s", b"S", b"v", b"p", b"L", b"i", b"BT", b"RPI", b"seq",
            ],
        )?;
        // BT — блок-сделка, RPI — улучшение цены розничного ордера: сделки настоящие, помечаются в столбце `x`
        let x: &[u8] = match (bt.boolean()?, rpi.boolean()?) {
            (false, false) => b"",
            (true, false) => b"B",
            (false, true) => b"R",
            (true, true) => b"BR",
        };
        let sell = match side.str()? {
            b"Sell" => true,
            b"Buy" => false,
            _ => return None,
        };
        let a = seq.int()?.checked_mul(TRADE_SLOT)?.checked_add(seq_idx)?;
        let uuid = uuid.str()?;
        // m: покупатель — мейкер, т.е. тейкер продаёт
        Some(Trade {
            sym: s.str()?,
            e: ts,
            t: t.int()?,
            a,
            // `a = seq·1000 + номер в пачке` у входов может различаться для одной сделки (нарезка пачек): личность — UUID сделки
            id: i64::from_ne_bytes(fnv(uuid).to_ne_bytes()),
            p: p.str()?,
            q: v.str()?,
            f: a,
            l: a,
            m: sell,
            nq: v.str()?,
            x,
            i: uuid,
        })
    }

    /// Пачка сделок поэлементно: годные (в том числе блок-сделки и RPI — с признаком в `x`) — записи; неизвестная сторона, новые поля и т. п. — один `raw` в кадре той же формы
    /// (номер в пачке — по исходной позиции, ключ `a` не сдвигается).
    fn trades(top: &Obj<'_>, out: &mut dyn Out) -> Option<usize> {
        if top.get(b"type")?.str()? != b"snapshot" {
            return None;
        }
        let ts = top.get(b"ts")?.int()?;
        let data = top.get(b"data")?.raw()?;
        let n = each_elem(data, |_| true)?;
        if n == 0 || i64::try_from(n).ok()? >= TRADE_SLOT {
            return None;
        }
        let (mut idx, mut good) = (0i64, 0usize);
        each_elem(data, |e| {
            good += usize::from(Self::fill(e, ts, idx).is_some());
            idx += 1;
            true
        })?;
        if good == 0 {
            return None;
        }
        let (mut idx, mut bad): (i64, Vec<u8>) = (0, Vec::new());
        each_elem(data, |e| {
            match Self::fill(e, ts, idx) {
                Some(t) => out.rec(&Rec::Trade(t)),
                None => {
                    bad.push(if bad.is_empty() { b'[' } else { b',' });
                    bad.extend_from_slice(e);
                }
            }
            idx += 1;
            true
        })?;
        if !bad.is_empty() {
            bad.push(b']');
            let mut w = Vec::with_capacity(bad.len() + 96);
            w.extend_from_slice(b"{\"topic\":\"");
            w.extend_from_slice(top.get(b"topic")?.str()?);
            w.extend_from_slice(b"\",\"type\":\"snapshot\",\"ts\":");
            w.extend_from_slice(ts.to_string().as_bytes());
            w.extend_from_slice(b",\"data\":");
            w.extend_from_slice(&bad);
            w.push(b'}');
            out.raw(&w);
        }
        Some(good)
    }

    fn tickers(&mut self, top: &Obj<'_>, out: &mut dyn Out) -> Option<usize> {
        let ts = top.get(b"ts")?.int()?;
        let ty = top.get(b"type")?.str()?;
        // в `data` десятки полей (цены, объёмы, стакан): нужны символ и четыре поля mark
        let [sym, mark, index, funding, next] = pick(
            top.get(b"data")?.raw()?,
            [
                b"symbol",
                b"markPrice",
                b"indexPrice",
                b"fundingRate",
                b"nextFundingTime",
            ],
        )?;
        let sym = sym?.str()?;
        let vals = [mark, index, funding, next];
        let st = if ty == b"snapshot" {
            let mut st = MarkState {
                ts,
                mark: Small::EMPTY,
                index: Small::EMPTY,
                funding: Small::EMPTY,
                next: Small::EMPTY,
            };
            for (i, v) in vals.iter().enumerate() {
                st.slot(i).set(v.as_ref()?.str()?)?;
            }
            self.marks.insert(sym.into(), st);
            self.marks.get(sym)?
        } else if ty == b"delta" {
            // дельта до снапшота или запоздавшая с другого пути: состояние не портим, писать нечего
            let Some(st) = self.marks.get_mut(sym) else {
                return Some(0);
            };
            if ts < st.ts || vals.iter().all(Option::is_none) {
                return Some(0);
            }
            st.ts = ts;
            for (i, v) in vals.iter().enumerate() {
                if let Some(v) = v {
                    st.slot(i).set(v.str()?)?;
                }
            }
            &*st
        } else {
            return None;
        };
        let next = match st.next.get() {
            [] => 0,
            s => parse_i64(s)?,
        };
        out.rec(&Rec::Mark(Mark {
            sym,
            e: ts,
            p: st.mark.get(),
            ap: b"",
            pp: b"",
            i: st.index.get(),
            r: st.funding.get(),
            t: next,
        }));
        Some(1)
    }
}

impl Exchange for Bybit {
    fn name(&self) -> &'static str {
        "bybit"
    }

    fn kinds(&self) -> &'static [&'static str] {
        &["book", "trade", "mark"]
    }

    fn per_conn(&self, _kind: &str) -> usize {
        80
    }

    fn plan(&self, pool: &[String], kinds: &[String], per_conn: usize) -> Vec<ShardPlan> {
        let mut out = Vec::new();
        for kind in kinds {
            let prefix = match kind.as_str() {
                "book" => "orderbook.1.",
                "trade" => "publicTrade.",
                "mark" => "tickers.",
                _ => continue,
            };
            let per = if per_conn == 0 {
                self.per_conn(kind)
            } else {
                per_conn
            };
            let topics: Vec<String> = pool.iter().map(|s| format!("{prefix}{s}")).collect();
            for (i, chunk) in balanced_chunks(&topics, per).into_iter().enumerate() {
                out.push(ShardPlan {
                    name: format!("{kind}{i:02}"),
                    endpoint: "linear".into(),
                    streams: chunk,
                });
            }
        }
        out
    }

    fn url(&self, _endpoint: &str, host: &str) -> String {
        format!("wss://{host}{PATH}")
    }

    /// Как в Python (`bybit_ws.entrances`, g022-integrate): линия 0 — com по IPv4, запасной — com по IPv6 (только с `--allow-ipv6`; без флага IPv6 нет нигде, kz/georgia — только IPv4-адреса имён); линия 1 — kz, запасной — georgia
    /// (другая сеть, не CloudFront); линии не делят вход.
    fn default_entrances(&self, lanes: usize) -> Vec<Vec<Entrance>> {
        let lines = [
            vec![
                Entrance::new(HOST_COM, Family::V4),
                Entrance::new(HOST_COM, Family::V6),
            ],
            vec![
                Entrance::new(HOST_KZ, Family::Any),
                Entrance::new(HOST_GE, Family::Any),
            ],
        ];
        (0..lanes)
            .map(|k| {
                lines
                    .get(k)
                    .or_else(|| lines.get(k % 2))
                    .cloned()
                    .unwrap_or_default()
            })
            .collect()
    }

    fn subscribe_messages(&self, streams: &[String]) -> Vec<String> {
        streams
            .chunks(SUB_BATCH)
            .map(|c| json!({"op": "subscribe", "args": c}).to_string())
            .collect()
    }

    fn sub_period(&self) -> Duration {
        Duration::from_millis(200)
    }

    fn new_parser(&self) -> Box<dyn Parser> {
        Box::new(BybitParser {
            marks: HashMap::new(),
        })
    }

    fn lag_ms(&self, frame: &[u8], rx_ns: i64) -> Option<f64> {
        let ts = parse_object(frame)?.get(b"ts")?.int()?;
        Some(lag_ms(rx_ns, ts))
    }

    fn symbol<'a>(&self, frame: &'a [u8]) -> Option<&'a [u8]> {
        let rest = frame.strip_prefix(b"{\"topic\":\"")?;
        let end = rest.iter().position(|&c| c == b'"')?;
        rest.get(..end)?.rsplit(|&c| c == b'.').next()
    }

    fn load_pool(
        &self,
        min_qv: f64,
        top_exclude: usize,
    ) -> Result<(Vec<String>, Vec<String>), String> {
        let mut instruments = Vec::new();
        let mut cursor = String::new();
        loop {
            let suffix = if cursor.is_empty() {
                String::new()
            } else {
                format!("&cursor={cursor}")
            };
            let page = http_get_json(&format!(
                "{REST}/v5/market/instruments-info?category=linear&limit=1000{suffix}"
            ))?;
            let result = page.get("result").ok_or("instruments-info: нет result")?;
            instruments.extend(
                result
                    .get("list")
                    .and_then(|x| x.as_array())
                    .cloned()
                    .unwrap_or_default(),
            );
            cursor = result
                .get("nextPageCursor")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            if cursor.is_empty() {
                break;
            }
        }
        let tickers = http_get_json(&format!("{REST}/v5/market/tickers?category=linear"))?;
        let list = tickers
            .get("result")
            .and_then(|r| r.get("list"))
            .cloned()
            .unwrap_or_default();
        Ok(select_pool(&instruments, &list, min_qv, top_exclude))
    }
}

/// `instruments` — instruments-info['list'], `tickers` — tickers['list'] (turnover24h, $).
pub fn select_pool(
    instruments: &[serde_json::Value],
    tickers: &serde_json::Value,
    min_qv: f64,
    top_exclude: usize,
) -> (Vec<String>, Vec<String>) {
    let mut qv: BTreeMap<String, f64> = BTreeMap::new();
    for t in tickers.as_array().into_iter().flatten() {
        let v = t
            .get("turnover24h")
            .and_then(|x| x.as_str())
            .and_then(|x| x.parse::<f64>().ok());
        if let (Some(sym), Some(v)) = (t.get("symbol").and_then(|x| x.as_str()), v) {
            if v >= 0.0 {
                qv.insert(sym.to_string(), v);
            }
        }
    }
    let ok: BTreeMap<String, f64> = instruments
        .iter()
        .filter(|s| {
            s.get("status").and_then(|x| x.as_str()) == Some("Trading")
                && s.get("contractType").and_then(|x| x.as_str()) == Some("LinearPerpetual")
                && s.get("quoteCoin").and_then(|x| x.as_str()) == Some("USDT")
        })
        .filter_map(|s| {
            let name = s.get("symbol")?.as_str()?;
            Some((name.to_string(), *qv.get(name)?))
        })
        .collect();
    rank_pool(&ok, min_qv, top_exclude)
}
