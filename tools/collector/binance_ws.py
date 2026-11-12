#!/usr/bin/env python3
"""TK-527 (G-022, шаг 1): адаптер Binance USD-M для общего ядра `wscore` (В-142): только специфика биржи — адреса, подписка, пул, разбор кадра.

Виды: bookTicker (эндпоинт /public), aggTrade и markPrice `!markPrice@arr@1s` (эндпоинт /market); L1 + сделки + mark/фандинг, без глубины (В-115).
Формат записи, соединения, пинг, путь, писатель, health — в пакете `wscore/` (карта модулей — `wscore/__init__.py`, контракт адаптера — `wscore/session.py`). Запуск: binance_ws.py --root /data/bnws [--health /data/health/binance.json]
"""
import json
import statistics
import sys
import urllib.request

import orjson

from wscore.pool import rank_pool
from wscore.runner import collector_main

REST = "https://fapi.binance.com"
WS = "wss://fstream.binance.com"
KINDS = {"book": ("public", "bookTicker"), "trade": ("market", "aggTrade"), "mark": ("market", "!markPrice@arr@1s")}
SUB_BATCH = 100  # потоков в одном SUBSCRIBE
EXPECT_BOOK = {"e", "u", "s", "ps", "b", "B", "a", "A", "T", "E", "st"}
EXPECT_TRADE = {"e", "E", "a", "s", "p", "q", "nq", "f", "l", "T", "m", "st"}
EXPECT_MARK = {"e", "E", "s", "p", "ap", "P", "i", "r", "T", "st"}


def select_pool(symbols, tickers, min_qv, top_exclude):
    """symbols — exchangeInfo['symbols'], tickers — список ticker/24hr. Возвращает (пул, исключённые топ)."""
    qv = {}
    for t in tickers:
        try:
            v = float(t["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if v >= 0:
            qv[t["symbol"]] = v
    ok = {
        s["symbol"]: qv[s["symbol"]]
        for s in symbols
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s["symbol"] in qv
    }
    return rank_pool(ok, min_qv, top_exclude)


def balanced_groups(pool, weights, per_conn):
    """Символы по соединениям так, чтобы сумма активности была ровной (TK-531): по убыванию веса — в соединение с наименьшей суммой, где ещё есть место (не больше per_conn).
    weights — символ -> измеренная активность (строк книги); символ без измерения (новый в пуле) получает медиану измеренных. Порядок детерминирован (вес, имя)."""
    k = -(-len(pool) // per_conn)
    default = statistics.median(weights.values()) if weights else 0.0
    w = {s: weights.get(s, default) for s in pool}
    groups, load = [[] for _ in range(k)], [0.0] * k
    for s in sorted(pool, key=lambda x: (-w[x], x)):
        i = min((j for j in range(k) if len(groups[j]) < per_conn), key=lambda j: (load[j], j))
        groups[i].append(s)
        load[i] += w[s]
    return [sorted(g) for g in groups]


def shard_streams(pool, kind, per_conn, weights=None):
    """Потоки вида kind, раскладка по соединениям по per_conn штук: [(шард, [stream,...]), ...]. mark — один общий поток.
    Без weights — куски пула по алфавиту; с weights (только книга) — по активности, balanced_groups."""
    suffix = KINDS[kind][1]
    if kind == "mark":
        return [("mark00", [suffix])]
    if weights and kind == "book":
        return [(f"{kind}{i:02d}", [f"{s.lower()}@{suffix}" for s in g]) for i, g in enumerate(balanced_groups(pool, weights, per_conn))]
    streams = [f"{s.lower()}@{suffix}" for s in pool]
    return [(f"{kind}{i // per_conn:02d}", streams[i : i + per_conn]) for i in range(0, len(streams), per_conn)]


def fetch_json(path):
    with urllib.request.urlopen(REST + path, timeout=30) as r:
        return json.load(r)


class Adapter:
    name = "binance"
    health_default = "/data/health/binance.json"
    streams_per_conn = 64  # замер TK-527: 256/128 потоков — хроническое отставание и разрывы, 64 — у 1 из 8
    min_quote_volume = 300_000.0
    top_exclude = 10
    kinds = ("book", "trade", "mark")
    trade_ids_consecutive = True  # aggTrade a идут подряд по символу: приёмка считает пропуски
    sub_period_s = 0.2  # лимит 10 входящих сообщений/с на соединение -> 5/с
    weights = None  # символ -> активность книги (runner --weights): раскладка книги по активности; None — по алфавиту

    def load_pool(self, min_qv, top_exclude):
        return select_pool(fetch_json("/fapi/v1/exchangeInfo")["symbols"], fetch_json("/fapi/v1/ticker/24hr"), min_qv, top_exclude)

    def plan(self, pool, kinds, per_conn):
        return [(name, KINDS[k][0], streams) for k in kinds for name, streams in shard_streams(pool, k, per_conn, self.weights)]

    def url(self, endpoint):
        return f"{WS}/{endpoint}/stream"

    def subscribe_messages(self, streams):
        return [json.dumps({"method": "SUBSCRIBE", "params": streams[i : i + SUB_BATCH], "id": i + 1}) for i in range(0, len(streams), SUB_BATCH)]

    def parse(self, frame):
        """Кадр -> записи ядра; None — не разобрано точно (ответ на SUBSCRIBE, новые поля биржи): ядро пишет кадр как пришёл."""
        try:
            d = orjson.loads(frame)["data"]
            if type(d) is dict:
                k = d.keys()
                if k == EXPECT_BOOK and d["ps"] == d["s"] and d["st"] == 1:
                    return [("b", d["s"], d["E"], d["T"], d["u"], d["b"], d["B"], d["a"], d["A"])]
                if k == EXPECT_TRADE and d["st"] == 1 and type(d["m"]) is bool:
                    return [("t", d["s"], d["E"], d["T"], d["a"], d["p"], d["q"], d["f"], d["l"], d["m"], d["nq"])]
            elif type(d) is list and d and all(type(x) is dict and x.keys() == EXPECT_MARK and x["e"] == "markPriceUpdate" and x["st"] == 1 for x in d):
                return [("m", x["s"], x["E"], x["p"], x["ap"], x["P"], x["i"], x["r"], x["T"]) for x in d]
        except (ValueError, KeyError, TypeError):
            pass
        return None

    def symbol(self, frame):
        if frame.startswith('{"stream":"') and frame[11] != "!":
            return frame[11 : frame.find("@", 11)]
        return None

    def lag_ms(self, frame, rx_ns):
        """Лаг прихода кадра, мс (время приёма − E биржи); None — в кадре нет E."""
        try:
            d = orjson.loads(frame).get("data")
            if type(d) is list:
                d = d[0] if d else None
            if d and "E" in d:
                return rx_ns / 1e6 - d["E"]
        except (ValueError, AttributeError, TypeError):
            pass
        return None


def main():
    collector_main(Adapter())


if __name__ == "__main__":
    sys.exit(main())
