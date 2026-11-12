import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import binance_ws as bw  # noqa: E402

AD = bw.Adapter()


def sym(name, status="TRADING", ctype="PERPETUAL", quote="USDT"):
    return {"symbol": name, "status": status, "contractType": ctype, "quoteAsset": quote}


def tick(name, qv):
    return {"symbol": name, "quoteVolume": qv}


def test_pool_excludes_top_and_threshold():
    names = [f"A{i:02d}USDT" for i in range(15)]
    syms = [sym(n) for n in names]
    tks = [tick(n, str(1_000_000 * (20 - i))) for i, n in enumerate(names)]
    tks[14] = tick(names[14], "300000")  # ровно порог — входит
    pool, top = bw.select_pool(syms, tks, 300_000.0, 10)
    assert top == names[:10]
    assert pool == sorted(names[10:])
    tks[14] = tick(names[14], "299999.99")
    pool, _ = bw.select_pool(syms, tks, 300_000.0, 10)
    assert names[14] not in pool


def test_pool_filters_bad_rows():
    syms = [sym("OKUSDT"), sym("QTRUSDT", ctype="CURRENT_QUARTER"), sym("BUSDUSD", quote="USD"),
            sym("SETUSDT", status="SETTLING"), sym("ZEROUSDT"), sym("EMPTYUSDT"), sym("NEGUSDT"), sym("NOTICKUSDT")]
    tks = [tick("OKUSDT", "5000000"), tick("QTRUSDT", "9e9"), tick("BUSDUSD", "9e9"), tick("SETUSDT", "9e9"),
           tick("ZEROUSDT", "0"), tick("EMPTYUSDT", ""), tick("NEGUSDT", "-5")]
    pool, top = bw.select_pool(syms, tks, 300_000.0, 0)
    assert pool == ["OKUSDT"] and top == []


def test_pool_small():
    pool, top = bw.select_pool([sym("AUSDT")], [tick("AUSDT", "9000000")], 300_000.0, 10)
    assert pool == [] and top == ["AUSDT"]


def test_shards_limit_and_lowercase():
    pool = [f"S{i:03d}USDT" for i in range(512)]
    sh = bw.shard_streams(pool, "book", 256)
    assert [n for n, _ in sh] == ["book00", "book01"]
    assert all(len(s) <= 256 for _, s in sh)
    assert sh[0][1][0] == "s000usdt@bookTicker"
    tr = bw.shard_streams(pool, "trade", 200)
    assert len(tr) == 3 and tr[2][1][-1] == "s511usdt@aggTrade"
    assert sum(len(s) for _, s in tr) == 512


def test_balanced_layout_covers_pool_evenly():
    pool = [f"S{i:03d}USDT" for i in range(130)]
    w = {s: (1000.0 if i % 13 == 0 else 10.0 + i) for i, s in enumerate(pool)}  # несколько тяжёлых символов
    sh = bw.shard_streams(pool, "book", 64, w)
    assert [n for n, _ in sh] == ["book00", "book01", "book02"]
    flat = [x for _, g in sh for x in g]
    assert sorted(flat) == sorted(f"{s.lower()}@bookTicker" for s in pool)  # каждый символ ровно один раз
    assert all(len(g) <= 64 for _, g in sh)
    load = [sum(w[x.split("@")[0].upper()] for x in g) for _, g in sh]
    plain = [sum(w[s] for s in pool[i : i + 64]) for i in range(0, 130, 64)]
    assert max(load) - min(load) < max(plain) - min(plain)
    assert sh == bw.shard_streams(pool, "book", 64, dict(reversed(list(w.items()))))  # порядок словаря не важен


def test_balanced_layout_unknown_symbol_gets_median_and_kinds_ignore_weights():
    pool = ["AUSDT", "BUSDT", "CUSDT", "NEWUSDT"]
    g = bw.balanced_groups(pool, {"AUSDT": 100.0, "BUSDT": 10.0, "CUSDT": 30.0}, 2)
    assert g == [["AUSDT", "BUSDT"], ["CUSDT", "NEWUSDT"]]  # NEW = медиана (30): тяжёлый A с лёгким B, C с новым
    tr = bw.shard_streams(pool, "trade", 2, {"AUSDT": 1.0})
    assert [x for _, grp in tr for x in grp] == [f"{s.lower()}@aggTrade" for s in pool]  # сделки — по алфавиту, веса книги к ним не относятся
    assert bw.shard_streams(pool, "book", 2, None) == bw.shard_streams(pool, "book", 2)


def test_mark_shard_single_stream():
    assert bw.shard_streams(["AUSDT"], "mark", 64) == [("mark00", ["!markPrice@arr@1s"])]


BOOK = {"e": "bookTicker", "u": 12081389761850, "s": "AVAXUSDT", "ps": "AVAXUSDT", "b": "8.6790", "B": "42", "a": "8.6800", "A": "285", "T": 1794418695170, "E": 1794418695171, "st": 1}
TRADE = {"e": "aggTrade", "E": 1794418694042, "a": 92150638, "s": "AIOTUSDT", "p": "0.1742400", "q": "410", "nq": "410", "f": 180333636, "l": 180333637, "T": 1794418693917, "m": True, "st": 1}
MARK = {"e": "markPriceUpdate", "E": 1794418695000, "s": "AVAXUSDT", "p": "8.68001", "ap": "8.68002", "P": "8.67", "st": 1, "i": "8.6801", "r": "0.00010000", "T": 1794441600000}


def frame(stream, data):
    return json.dumps({"stream": stream, "data": data}, separators=(",", ":"))


def frame(stream, data):
    return json.dumps({"stream": stream, "data": data}, separators=(",", ":"))



def test_plan_subscribe_and_endpoints():
    pool = [f"S{i:03d}USDT" for i in range(130)]
    plan = AD.plan(pool, ["book", "trade", "mark"], 64)
    assert [(n, e) for n, e, _ in plan] == [("book00", "public"), ("book01", "public"), ("book02", "public"), ("trade00", "market"), ("trade01", "market"), ("trade02", "market"), ("mark00", "market")]
    assert AD.url("public") == "wss://fstream.binance.com/public/stream"
    msgs = AD.subscribe_messages(plan[0][2] + plan[1][2])  # 128 потоков -> 2 сообщения по SUB_BATCH
    assert [len(json.loads(m)["params"]) for m in msgs] == [100, 28] and json.loads(msgs[0])["method"] == "SUBSCRIBE"


def test_parse_maps_frames_to_core_records():
    b = AD.parse(frame("avaxusdt@bookTicker", BOOK))
    assert b == [("b", "AVAXUSDT", BOOK["E"], BOOK["T"], BOOK["u"], "8.6790", "42", "8.6800", "285")]
    t = AD.parse(frame("aiotusdt@aggTrade", TRADE))
    assert t == [("t", "AIOTUSDT", TRADE["E"], TRADE["T"], TRADE["a"], "0.1742400", "410", TRADE["f"], TRADE["l"], True, "410")]
    m = AD.parse(frame("!markPrice@arr@1s", [MARK, dict(MARK, s="AIOTUSDT")]))
    assert [r[:2] for r in m] == [("m", "AVAXUSDT"), ("m", "AIOTUSDT")] and m[0][-1] == MARK["T"]


def test_parse_unexpected_frames_return_none():
    cases = ['{"result":null,"id":1}', "не json", "", '{"stream":"x@bookTicker"}', '{"stream":"x","data":5}',
             frame("a@bookTicker", dict(BOOK, new_field=1)), frame("a@bookTicker", dict(BOOK, ps="OTHER")),
             frame("a@aggTrade", dict(TRADE, st=2)), frame("!markPrice@arr@1s", []), frame("!markPrice@arr@1s", [dict(MARK, x=1)])]
    for c in cases:
        assert AD.parse(c) is None, c
    assert AD.parse(frame("a@aggTrade", dict(TRADE, nq="999")))[0][-1] == "999"  # RPI-сделка nq != q — запись t, не сырой кадр


def test_symbol_key():
    assert AD.symbol(frame("avaxusdt@bookTicker", BOOK)) == "avaxusdt"
    assert AD.symbol(frame("!markPrice@arr@1s", [MARK])) is None
    assert AD.symbol('{"result":null,"id":1}') is None


def test_lag_ms():
    rx_ns = 1_000_000_000_000 * 1_000_000
    fr = json.dumps({"stream": "btcusdt@bookTicker", "data": {"E": 1_000_000_000_000 - 250}})
    assert AD.lag_ms(fr, rx_ns) == pytest.approx(250)
    assert AD.lag_ms(json.dumps({"data": [{"E": 1_000_000_000_000 - 40}]}), rx_ns) == pytest.approx(40)
    for bad in ("", "не json", "{}", '{"data": []}', '{"data": {"x": 1}}'):
        assert AD.lag_ms(bad, rx_ns) is None
