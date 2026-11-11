#!/usr/bin/env python3
"""TK-532 (G-022, шаг 2): адаптер Bybit USDT-бессрочных (v5 public linear) для общего ядра `wscore` (В-142): только специфика биржи — адреса, подписка, пул, разбор кадра.

Виды: orderbook.1 (L1), publicTrade (сделки), tickers (mark/index/фандинг; дельты сливаются в состояние символа). Глубины нет (В-115). Один эндпоинт на все виды.
Отличия от Binance, снятые живым замером 12.11 (srv, Нидерланды): сделки пачкой на один ордер тейкера с общим seq и T, id сделки — UUID без порядка; L1 всегда type=snapshot, повтор кадра с тем же u.
Запуск: bybit_ws.py --root /data/bbws [--health /data/health/bybit.json]
"""
import json
import sys
import urllib.request

import orjson

from wscore.pool import rank_pool
from wscore.runner import collector_main

REST = "https://api.bybit.com"
WS_URL = "wss://stream.bybit.com/v5/public/linear"
TOPICS = {"book": "orderbook.1.{}", "trade": "publicTrade.{}", "mark": "tickers.{}"}
SUB_BATCH = 100  # топиков в одном subscribe (лимит Bybit linear — 21 000 знаков args на запрос; 100 топиков ≈ 2,8 тыс.)
TRADE_SLOT = 1000  # место сделки в пачке с общим seq; пачка Bybit — до сотен сделок, 1000 с запасом (больше — кадр пишется сырым)
EXPECT_BOOK = {"topic", "ts", "type", "data", "cts"}
EXPECT_BOOK_DATA = {"s", "b", "a", "u", "seq"}
EXPECT_TRADE = {"T", "s", "S", "v", "p", "L", "i", "BT", "RPI", "seq"}
MARK_FIELDS = ("markPrice", "indexPrice", "fundingRate", "nextFundingTime")


def select_pool(instruments, tickers, min_qv, top_exclude):
    """instruments — instruments-info['list'], tickers — tickers['list'] (turnover24h, $). Возвращает (пул, исключённые топ)."""
    qv = {}
    for t in tickers:
        try:
            v = float(t["turnover24h"])
        except (KeyError, ValueError, TypeError):
            continue
        if v >= 0:
            qv[t["symbol"]] = v
    ok = {
        s["symbol"]: qv[s["symbol"]]
        for s in instruments
        if s.get("status") == "Trading"
        and s.get("contractType") == "LinearPerpetual"
        and s.get("quoteCoin") == "USDT"
        and s["symbol"] in qv
    }
    return rank_pool(ok, min_qv, top_exclude)


def shard_streams(pool, kind, per_conn):
    """Топики вида kind по per_conn штук на соединение: [(шард, [topic,...]), ...]."""
    topics = [TOPICS[kind].format(s) for s in pool]
    return [(f"{kind}{i // per_conn:02d}", topics[i : i + per_conn]) for i in range(0, len(topics), per_conn)]


def fetch_json(path):
    with urllib.request.urlopen(REST + path, timeout=30) as r:
        return json.load(r)


def fetch_instruments():
    """instruments-info linear по страницам (cursor)."""
    out, cursor = [], ""
    while True:
        r = fetch_json("/v5/market/instruments-info?category=linear&limit=1000" + (f"&cursor={cursor}" if cursor else ""))["result"]
        out += r["list"]
        cursor = r.get("nextPageCursor") or ""
        if not cursor:
            return out


class Adapter:
    name = "bybit"
    health_default = "/data/health/bybit.json"
    streams_per_conn = 100  # старт: замер лагов и обрывов по окнам 3 мин (В-141, В-144) подберёт число
    min_quote_volume = 300_000.0
    top_exclude = 10
    kinds = ("book", "trade", "mark")
    trade_ids_consecutive = False  # a = seq*TRADE_SLOT + номер в пачке: растёт, но не подряд
    sub_period_s = 0.1

    def __init__(self):
        self.mark = {}  # символ -> {поле: значение, "ts": ts последнего применённого кадра}: tickers шлёт дельты, запись m — полное состояние

    def load_pool(self, min_qv, top_exclude):
        return select_pool(fetch_instruments(), fetch_json("/v5/market/tickers?category=linear")["result"]["list"], min_qv, top_exclude)

    def plan(self, pool, kinds, per_conn):
        return [(name, "linear", topics) for k in kinds for name, topics in shard_streams(pool, k, per_conn)]

    def url(self, endpoint):
        return WS_URL

    def subscribe_messages(self, streams):
        return [json.dumps({"op": "subscribe", "args": streams[i : i + SUB_BATCH]}) for i in range(0, len(streams), SUB_BATCH)]

    def parse(self, frame):
        """Кадр -> записи ядра; None — не разобрано точно (ответ на subscribe/pong, новые поля биржи, блок-сделка): ядро пишет кадр как пришёл. [] — разобран, писать нечего."""
        try:
            d = orjson.loads(frame)
            topic = d.get("topic")
            if topic is None:
                return None
            kind = topic.partition(".")[0]
            if kind == "orderbook":
                return self._book(d)
            if kind == "publicTrade":
                return self._trades(d)
            if kind == "tickers":
                return self._mark(d)
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        return None

    @staticmethod
    def _book(d):
        x = d["data"]
        if d.keys() != EXPECT_BOOK or d["type"] != "snapshot" or x.keys() != EXPECT_BOOK_DATA or len(x["b"]) != 1 or len(x["a"]) != 1:
            return None
        (bp, bq), (ap, aq) = x["b"][0], x["a"][0]
        return [("b", x["s"], d["ts"], d["cts"], x["u"], bp, bq, ap, aq)]

    @staticmethod
    def _trades(d):
        xs = d["data"]
        if d["type"] != "snapshot" or not xs or len(xs) >= TRADE_SLOT:
            return None
        out = []
        for i, x in enumerate(xs):
            if x.keys() != EXPECT_TRADE or x["BT"] is not False or x["RPI"] is not False or x["S"] not in ("Buy", "Sell"):
                return None
            a = x["seq"] * TRADE_SLOT + i
            out.append(("t", x["s"], d["ts"], x["T"], a, x["p"], x["v"], a, a, x["S"] == "Sell", x["v"]))  # m: покупатель — мейкер, т.е. тейкер продаёт
        return out

    def _mark(self, d):
        x, ts = d["data"], d["ts"]
        s = x["symbol"]
        if d["type"] == "snapshot":
            st = self.mark[s] = {k: x[k] for k in MARK_FIELDS}
            st["ts"] = ts
        elif d["type"] == "delta":
            st = self.mark.get(s)
            if st is None or ts < st["ts"]:  # дельта до снапшота или запоздавшая с другого пути: состояние не портим
                return []
            if not any(k in x for k in MARK_FIELDS):  # изменились bid/ask/last/объём — не mark
                return []
            st["ts"] = ts
            for k in MARK_FIELDS:
                if k in x:
                    st[k] = x[k]
        else:
            return None
        return [("m", s, ts, st["markPrice"], "", "", st["indexPrice"], st["fundingRate"], int(st["nextFundingTime"] or 0))]

    def symbol(self, frame):
        if frame.startswith('{"topic":"'):
            return frame[10 : frame.find('"', 10)].rpartition(".")[2].lower()
        return None

    def lag_ms(self, frame, rx_ns):
        """Лаг прихода кадра, мс (время приёма − ts биржи); None — в кадре нет ts."""
        try:
            ts = orjson.loads(frame).get("ts")
            if ts is not None:
                return rx_ns / 1e6 - ts
        except (ValueError, AttributeError, TypeError):
            pass
        return None


def main():
    collector_main(Adapter())


if __name__ == "__main__":
    sys.exit(main())
