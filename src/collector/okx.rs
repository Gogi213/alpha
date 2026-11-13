//! Адаптер OKX public WebSocket v5 (линейные USDT-бессрочные SWAP) для ядра коллектора: адреса, подписка, пул, разбор кадра.
//!
//! Виды (`docs/findings/okx-ws-prep-2026-11-12.md`): book — `bbo-tbt` (верх книги, снимок каждые 10 мс при изменении, число ордеров на уровне),
//! trade — `trades` (агрегат по ордеру тейкера: `tradeId` — последний id, `count` — сколько сделок; `source` 1 — ELP), mark — `mark-price` (каждый
//! кадр — запись, без прореживания: В-172) + `index-tickers` и `funding-rate` (дельты сливаются в состояние символа, как `tickers` у Bybit).
//! Один эндпоинт `/ws/v5/public` на все виды; кадр — `{"arg":{"channel","instId"},"data":[{…}]}`, времена — миллисекунды строкой.
//! Разбор строгий: набор ключей элемента точно как в замерах 12.11, иначе элемент (или весь кадр) пишется как пришёл. Первые кадры `mark-price`
//! до первых `index-tickers` и `funding-rate` символа (≈ 1 с после подписки) пишутся сырыми: запись `m` без индекса и ставки была бы неполной.
//! Порядок и личность записей: `seqId` (book), `tradeId` (trade), `ts` кадра `mark-price` (mark) — одинаковы на разных соединениях.

use std::collections::{BTreeMap, HashMap};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Map, Value};

use super::codec::{Book, Mark, Rec, Trade};
use super::exchange::{lag_ms, Entrance, Exchange, Family, Out, Parsed, Parser, ShardPlan};
use super::pool::{balanced_chunks, http_get_json_ua, rank_pool};
use super::scan::{each_elem, parse_i64, parse_object, pick, slots, Small, Val};

const REST: &str = "https://www.okx.com";
const HOST: &str = "ws.okx.com";
const PATH: &str = "/ws/v5/public";
/// Без `User-Agent` Cloudflare отвечает REST `403 error code: 1010` (замер 12.11).
const UA: &str = "alpha-collector/okx";
const SUB_BATCH: usize = 100;
const SUFFIX: &[u8] = b"-SWAP";
const META_KEYS: [&str; 9] = [
    "ctVal",
    "ctMult",
    "ctValCcy",
    "lotSz",
    "tickSz",
    "minSz",
    "instFamily",
    "instCategory",
    "listTime",
];

#[derive(Default)]
pub struct Okx {
    /// `meta` и `qv` пула (из последнего `load_pool`) — рядом с `pool-<день>.json`.
    extra: Mutex<Option<Value>>,
}

#[derive(Clone, Copy)]
struct Idx {
    ts: i64,
    px: Small,
}

#[derive(Clone, Copy)]
struct Fund {
    ts: i64,
    rate: Small,
    time: i64,
}

/// Состояние символов шарда: индекс (ключ — `instId` индекса, `BTC-USDT`) и фандинг (ключ — `instId` контракта, `BTC-USDT-SWAP`).
struct OkxParser {
    idx: HashMap<Box<[u8]>, Idx>,
    fund: HashMap<Box<[u8]>, Fund>,
}

impl Parser for OkxParser {
    fn parse(&mut self, frame: &[u8], out: &mut dyn Out) -> Parsed {
        self.parse_frame(frame, out)
            .map_or(Parsed::Raw, Parsed::Recs)
    }
}

/// `[["цена","размер","0","число ордеров"]]` — один уровень; третий элемент — устаревшее поле, всегда `"0"` (иначе — кадр сырым).
fn level(arr: &[u8]) -> Option<(&[u8], &[u8], &[u8])> {
    let mut lv: Option<&[u8]> = None;
    let n = each_elem(arr, |e| {
        lv = Some(e);
        true
    })?;
    if n != 1 {
        return None;
    }
    let mut f: [&[u8]; 4] = [&[], &[], &[], &[]];
    let mut k = 0usize;
    let m = each_elem(lv?, |e| {
        let inner = e.strip_prefix(b"\"").and_then(|x| x.strip_suffix(b"\""));
        match (inner, f.get_mut(k)) {
            (Some(s), Some(slot)) if !s.contains(&b'\\') && !s.contains(&b'"') => {
                *slot = s;
                k += 1;
                true
            }
            _ => false,
        }
    })?;
    let [price, size, zero, orders] = f;
    (m == 4 && zero == b"0" && !price.is_empty() && !size.is_empty() && !orders.is_empty())
        .then_some((price, size, orders))
}

fn ms(v: Val<'_>) -> Option<i64> {
    parse_i64(v.str()?)
}

/// Кадр той же формы, что исходный, но только с неразобранными элементами.
fn wrap(ch: &[u8], inst: &[u8], elems: &[u8]) -> Vec<u8> {
    let mut w = Vec::with_capacity(elems.len() + 64);
    w.extend_from_slice(b"{\"arg\":{\"channel\":\"");
    w.extend_from_slice(ch);
    w.extend_from_slice(b"\",\"instId\":\"");
    w.extend_from_slice(inst);
    w.extend_from_slice(b"\"},\"data\":");
    w.extend_from_slice(elems);
    w.push(b'}');
    w
}

impl OkxParser {
    fn parse_frame(&mut self, frame: &[u8], out: &mut dyn Out) -> Option<usize> {
        let top = parse_object(frame)?;
        let [arg, data] = slots(&top, [b"arg", b"data"])?;
        let arg = parse_object(arg.raw()?)?;
        let [ch, inst] = slots(&arg, [b"channel", b"instId"])?;
        let (ch, inst, data) = (ch.str()?, inst.str()?, data.raw()?);
        match ch {
            b"bbo-tbt" => Self::elems(ch, inst, data, out, |e, out| Self::book(inst, e, out)),
            b"trades" => Self::elems(ch, inst, data, out, |e, out| Self::trade(inst, e, out)),
            b"mark-price" => {
                let this = &*self;
                Self::elems(ch, inst, data, out, |e, out| this.mark(inst, e, out))
            }
            b"index-tickers" => {
                Self::elems(ch, inst, data, out, |e, _| self.index(inst, e).map(|()| 0))
            }
            b"funding-rate" => Self::elems(ch, inst, data, out, |e, _| {
                self.funding(inst, e).map(|()| 0)
            }),
            _ => None,
        }
    }

    /// Массив `data` поэлементно: годные элементы — записи (или состояние), негодные — один `raw` в кадре той же формы; ничего не теряется и не
    /// дублируется. Ни одного годного — `None` (кадр целиком сырым, `out` пуст). Форма массива проверяется до первого элемента.
    fn elems<'a>(
        ch: &[u8],
        inst: &[u8],
        data: &'a [u8],
        out: &mut dyn Out,
        mut one: impl FnMut(&'a [u8], &mut dyn Out) -> Option<usize>,
    ) -> Option<usize> {
        if each_elem(data, |_| true)? == 0 {
            return None;
        }
        let (mut good, mut recs, mut bad): (usize, usize, Vec<u8>) = (0, 0, Vec::new());
        each_elem(data, |e| {
            if let Some(n) = one(e, out) {
                good += 1;
                recs += n;
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
            out.raw(&wrap(ch, inst, &bad));
        }
        Some(recs)
    }

    fn book(inst: &[u8], e: &[u8], out: &mut dyn Out) -> Option<usize> {
        let o = parse_object(e)?;
        let [asks, bids, ts, seq] = slots(&o, [b"asks", b"bids", b"ts", b"seqId"])?;
        let (ask, asz, an) = level(asks.raw()?)?;
        let (bid, bsz, bn) = level(bids.raw()?)?;
        let t = ms(ts)?;
        let u = seq.int()?;
        out.rec(&Rec::Book(Book {
            sym: inst,
            e: t,
            t,
            u,
            id: u,
            bid,
            bsz,
            ask,
            asz,
            bn,
            an,
        }));
        Some(1)
    }

    /// `tradeId` — последний id агрегата, `count` — сколько сделок в нём (`f = tradeId − count + 1`, `l = tradeId`); `side` — сторона тейкера;
    /// `source` 1 (Enhanced Liquidity Program) — признак `E` в столбце `x`; `seqId` в формат не входит (у разных сделок бывает один).
    fn trade(inst: &[u8], e: &[u8], out: &mut dyn Out) -> Option<usize> {
        let o = parse_object(e)?;
        let [i, tid, px, sz, side, ts, count, source, seq] = slots(
            &o,
            [
                b"instId", b"tradeId", b"px", b"sz", b"side", b"ts", b"count", b"source", b"seqId",
            ],
        )?;
        if i.str()? != inst {
            return None;
        }
        seq.int()?;
        let id = ms(tid)?;
        let cnt = ms(count).filter(|c| *c >= 1)?;
        let t = ms(ts)?;
        let sell = match side.str()? {
            b"sell" => true,
            b"buy" => false,
            _ => return None,
        };
        let x: &[u8] = match source.str()? {
            b"0" => b"",
            b"1" => b"E",
            _ => return None,
        };
        out.rec(&Rec::Trade(Trade {
            sym: inst,
            e: t,
            t,
            a: id,
            id,
            p: px.str()?,
            q: sz.str()?,
            f: id.checked_sub(cnt)?.checked_add(1)?,
            l: id,
            m: sell,
            nq: sz.str()?,
            x,
            i: b"",
        }));
        Some(1)
    }

    /// Запись `m` на каждый кадр `mark-price`: цена — из кадра, индекс и ставка — последнее состояние символа (`index-tickers`, `funding-rate`);
    /// пока состояния нет — `None` (кадр сырым).
    fn mark(&self, inst: &[u8], e: &[u8], out: &mut dyn Out) -> Option<usize> {
        let o = parse_object(e)?;
        let [i, ty, px, ts] = slots(&o, [b"instId", b"instType", b"markPx", b"ts"])?;
        if i.str()? != inst || ty.str()? != b"SWAP" {
            return None;
        }
        let idx = self.idx.get(inst.strip_suffix(SUFFIX)?)?;
        let fund = self.fund.get(inst)?;
        out.rec(&Rec::Mark(Mark {
            sym: inst,
            e: ms(ts)?,
            p: px.str()?,
            ap: b"",
            pp: b"",
            i: idx.px.get(),
            r: fund.rate.get(),
            t: fund.time,
        }));
        Some(1)
    }

    /// Состояние индекса; кадр старше записанного состояния — ничего (запоздавший с другой линии не портит состояние).
    fn index(&mut self, inst: &[u8], e: &[u8]) -> Option<()> {
        let [i, px, ts] = pick(e, [b"instId", b"idxPx", b"ts"])?;
        if i?.str()? != inst {
            return None;
        }
        let (px, ts) = (px?.str()?, ms(ts?)?);
        if let Some(st) = self.idx.get_mut(inst) {
            if ts >= st.ts {
                st.px.set(px)?;
                st.ts = ts;
            }
        } else {
            let mut st = Idx {
                ts,
                px: Small::EMPTY,
            };
            st.px.set(px)?;
            self.idx.insert(inst.into(), st);
        }
        Some(())
    }

    fn funding(&mut self, inst: &[u8], e: &[u8]) -> Option<()> {
        let [i, rate, time, ts] = pick(e, [b"instId", b"fundingRate", b"fundingTime", b"ts"])?;
        if i?.str()? != inst {
            return None;
        }
        let (rate, time, ts) = (rate?.str()?, ms(time?)?, ms(ts?)?);
        if let Some(st) = self.fund.get_mut(inst) {
            if ts >= st.ts {
                st.rate.set(rate)?;
                st.ts = ts;
                st.time = time;
            }
        } else {
            let mut st = Fund {
                ts,
                rate: Small::EMPTY,
                time,
            };
            st.rate.set(rate)?;
            self.fund.insert(inst.into(), st);
        }
        Some(())
    }
}

/// Первый `ts` из `data[0]`: время биржи кадра (для лага и свежести сырых кадров).
fn first_ts(frame: &[u8]) -> Option<i64> {
    let top = parse_object(frame)?;
    let data = top.get(b"data")?.raw()?;
    let mut ts = None;
    each_elem(data, |e| {
        if ts.is_none() {
            ts = pick(e, [b"ts"]).and_then(|[t]| ms(t?));
        }
        true
    })?;
    ts
}

impl Exchange for Okx {
    fn name(&self) -> &'static str {
        "okx"
    }

    fn kinds(&self) -> &'static [&'static str] {
        &["book", "trade", "mark"]
    }

    /// Аргументов подписки на соединение: сто символов на вид (как в пробе P1: 200 и 300 аргументов на соединение, лаг p50 123–137 мс);
    /// в документации числа нет (подготовка OKX, п. 1) — потолок ищет лестница.
    fn per_conn(&self, kind: &str) -> usize {
        match kind {
            "mark" => 300,
            _ => 100,
        }
    }

    fn plan(&self, pool: &[String], kinds: &[String], per_conn: usize) -> Vec<ShardPlan> {
        let mut out = Vec::new();
        for kind in kinds {
            let channels: &[&str] = match kind.as_str() {
                "book" => &["bbo-tbt"],
                "trade" => &["trades"],
                "mark" => &["mark-price", "index-tickers", "funding-rate"],
                _ => continue,
            };
            let args = if per_conn == 0 {
                self.per_conn(kind)
            } else {
                per_conn
            };
            // символ целиком в одном шарде: состояние индекса и фандинга живёт в разборе шарда
            let symbols = (args / channels.len()).max(1);
            for (i, chunk) in balanced_chunks(pool, symbols).into_iter().enumerate() {
                let streams = chunk
                    .iter()
                    .flat_map(|s| {
                        channels.iter().map(move |ch| {
                            let inst = if *ch == "index-tickers" {
                                s.strip_suffix("-SWAP").unwrap_or(s)
                            } else {
                                s
                            };
                            format!("{ch}:{inst}")
                        })
                    })
                    .collect();
                out.push(ShardPlan {
                    name: format!("{kind}{i:02}"),
                    endpoint: "public".into(),
                    streams,
                });
            }
        }
        out
    }

    fn url(&self, _endpoint: &str, host: &str) -> String {
        format!("wss://{host}{PATH}")
    }

    /// Один хост `ws.okx.com` (независимой точки входа у OKX нет, подготовка п. 5): линия 0 — IPv4 с запасным IPv6, линия 1 — наоборот.
    fn default_entrances(&self, lanes: usize) -> Vec<Vec<Entrance>> {
        (0..lanes)
            .map(|k| {
                let (a, b) = if k % 2 == 0 {
                    (Family::V4, Family::V6)
                } else {
                    (Family::V6, Family::V4)
                };
                vec![Entrance::new(HOST, a), Entrance::new(HOST, b)]
            })
            .collect()
    }

    fn subscribe_messages(&self, streams: &[String]) -> Vec<String> {
        streams
            .chunks(SUB_BATCH)
            .map(|c| {
                let args: Vec<Value> = c
                    .iter()
                    .filter_map(|s| s.split_once(':'))
                    .map(|(ch, inst)| json!({"channel": ch, "instId": inst}))
                    .collect();
                json!({"op": "subscribe", "args": args}).to_string()
            })
            .collect()
    }

    /// Пауза между запросами подписки: 0,2 с — проверено в пробе (1 038 аргументов за 11 запросов, без `60014`); лимит биржи — 480 запросов/ч на соединение.
    fn sub_period(&self) -> Duration {
        Duration::from_millis(200)
    }

    fn new_parser(&self) -> Box<dyn Parser> {
        Box::new(OkxParser {
            idx: HashMap::new(),
            fund: HashMap::new(),
        })
    }

    fn lag_ms(&self, frame: &[u8], rx_ns: i64) -> Option<f64> {
        Some(lag_ms(rx_ns, first_ts(frame)?))
    }

    /// `instId` контракта из `arg`; кадры `index-tickers` несут индекс (`BTC-USDT`), не контракт — символа нет (запись `m` идёт от `mark-price`).
    fn symbol<'a>(&self, frame: &'a [u8]) -> Option<&'a [u8]> {
        let rest = frame.strip_prefix(b"{\"arg\":{\"channel\":\"")?;
        let q = rest.iter().position(|&c| c == b'"')?;
        let (ch, rest) = rest.split_at(q);
        if ch == b"index-tickers" {
            return None;
        }
        let rest = rest.strip_prefix(b"\",\"instId\":\"")?;
        let end = rest.iter().position(|&c| c == b'"')?;
        rest.get(..end)
    }

    fn load_pool(
        &self,
        min_qv: f64,
        top_exclude: usize,
    ) -> Result<(Vec<String>, Vec<String>), String> {
        let instruments = rest_data(&format!("{REST}/api/v5/public/instruments?instType=SWAP"))?;
        let tickers = rest_data(&format!("{REST}/api/v5/market/tickers?instType=SWAP"))?;
        let (pool, top, extra) = select_pool(&instruments, &tickers, min_qv, top_exclude);
        *self
            .extra
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(extra);
        Ok((pool, top))
    }

    /// Правило G-022 для OKX: весь пул, топ-10 не исключается (решение координатора: правило «без топ-10» — только Binance и Bybit).
    fn default_top_exclude(&self) -> usize {
        0
    }

    fn pool_meta(&self) -> Option<Value> {
        self.extra
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    /// `{"event":"notice","code":"64008",…}` — за 60 с до обновления сервиса: открыть замену, старое закрыть после перекрытия.
    fn is_retire_notice(&self, frame: &[u8]) -> bool {
        frame.starts_with(b"{\"event\":\"notice\"")
            && frame.windows(14).any(|w| w == b"\"code\":\"64008\"")
    }
}

fn rest_data(url: &str) -> Result<Vec<Value>, String> {
    let v = http_get_json_ua(url, Some(UA))?;
    if v.get("code").and_then(Value::as_str) != Some("0") {
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

/// `instruments` — `/public/instruments?instType=SWAP`, `tickers` — `/market/tickers?instType=SWAP`. Контракты `ctType=linear`, `settleCcy=USDT`,
/// `state=live`; оборот за сутки в долларах = `volCcy24h` (база) × `last` (в тикере OKX оборота в долларах нет — одно приближение на один запрос,
/// подготовка OKX п. 2). Возвращает (пул, исключённый топ, `{meta, qv}` пула: размер контракта и оборот для читателя и ранжирования ступеней).
pub fn select_pool(
    instruments: &[Value],
    tickers: &[Value],
    min_qv: f64,
    top_exclude: usize,
) -> (Vec<String>, Vec<String>, Value) {
    let mut turnover: BTreeMap<&str, f64> = BTreeMap::new();
    for t in tickers {
        let v = text(t, "volCcy24h")
            .and_then(|x| x.parse::<f64>().ok())
            .zip(text(t, "last").and_then(|x| x.parse::<f64>().ok()))
            .map(|(vol, last)| vol * last);
        if let (Some(id), Some(v)) = (text(t, "instId"), v) {
            if v >= 0.0 {
                turnover.insert(id, v);
            }
        }
    }
    let live: Vec<&Value> = instruments
        .iter()
        .filter(|s| {
            text(s, "ctType") == Some("linear")
                && text(s, "settleCcy") == Some("USDT")
                && text(s, "state") == Some("live")
        })
        .collect();
    let qv: BTreeMap<String, f64> = live
        .iter()
        .filter_map(|s| {
            let id = text(s, "instId")?;
            Some((id.to_string(), *turnover.get(id)?))
        })
        .collect();
    let (pool, top) = rank_pool(&qv, min_qv, top_exclude);
    let mut meta = Map::new();
    let mut vols = Map::new();
    for s in &live {
        let Some(id) = text(s, "instId").filter(|id| pool.iter().any(|p| p == id)) else {
            continue;
        };
        let m: Map<String, Value> = META_KEYS
            .iter()
            .filter_map(|k| Some(((*k).to_string(), s.get(*k)?.clone())))
            .collect();
        meta.insert(id.to_string(), Value::Object(m));
        if let Some(v) = qv.get(id) {
            vols.insert(id.to_string(), json!((v * 100.0).round() / 100.0));
        }
    }
    (pool, top, json!({"meta": meta, "qv": vols}))
}
