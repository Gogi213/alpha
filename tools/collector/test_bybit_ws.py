import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import bybit_ws as bw  # noqa: E402
from wscore import codec  # noqa: E402

BOOK = '{"topic":"orderbook.1.BTCUSDT","ts":1794433022399,"type":"snapshot","data":{"s":"BTCUSDT","b":[["74600","8.454"]],"a":[["74600.1","7.416"]],"u":174478070,"seq":843868169089},"cts":1794433022398}'
TRADE = '{"topic":"publicTrade.XRPUSDT","type":"snapshot","ts":1794433203184,"data":[%s]}'
FILL = '{"T":1794433203183,"s":"XRPUSDT","S":"%s","v":"%s","p":"1.0634","L":"PlusTick","i":"%s","BT":%s,"RPI":false,"seq":272596956733}'
TICK_SNAP = ('{"topic":"tickers.BTCUSDT","type":"snapshot","data":{"symbol":"BTCUSDT","lastPrice":"74600.00","markPrice":"74601.07","indexPrice":"74634.68",'
             '"fundingRate":"0.00006514","nextFundingTime":"1794441600000","bid1Price":"74600.00","openInterest":"69022.136"},"cs":1,"ts":1794433021421}')
TICK_DELTA = '{"topic":"tickers.BTCUSDT","type":"delta","data":%s,"cs":2,"ts":%d}'


def inst(name, status="Trading", ctype="LinearPerpetual", quote="USDT"):
    return {"symbol": name, "status": status, "contractType": ctype, "quoteCoin": quote}


def test_pool_rule_and_filters():
    names = [f"A{i:02d}USDT" for i in range(15)]
    ins = [inst(n) for n in names] + [inst("FUTUSDT", ctype="LinearFutures"), inst("USDCPERP", quote="USDC"), inst("PREUSDT", status="PreLaunch"), inst("NOTICKUSDT")]
    tks = [{"symbol": n, "turnover24h": str(1_000_000 * (20 - i))} for i, n in enumerate(names)]
    tks += [{"symbol": x, "turnover24h": "9e9"} for x in ("FUTUSDT", "USDCPERP", "PREUSDT")] + [{"symbol": "BADUSDT", "turnover24h": ""}]
    tks[14]["turnover24h"] = "300000"  # ровно порог — входит
    pool, top = bw.select_pool(ins, tks, 300_000.0, 10)
    assert top == names[:10] and pool == sorted(names[10:])
    tks[14]["turnover24h"] = "299999.99"
    assert names[14] not in bw.select_pool(ins, tks, 300_000.0, 10)[0]


def test_plan_subscribe():
    ad = bw.Adapter()
    pool = [f"S{i:03d}USDT" for i in range(250)]
    plan = ad.plan(pool, ad.kinds, 100)
    assert [(n, len(t)) for n, _, t in plan] == [("book00", 100), ("book01", 100), ("book02", 50), ("trade00", 100), ("trade01", 100), ("trade02", 50), ("mark00", 100), ("mark01", 100), ("mark02", 50)]
    assert plan[0][2][0] == "orderbook.1.S000USDT" and plan[3][2][0] == "publicTrade.S000USDT" and plan[6][2][0] == "tickers.S000USDT"
    msgs = ad.subscribe_messages(plan[0][2])
    assert len(msgs) == 1 and json.loads(msgs[0]) == {"op": "subscribe", "args": plan[0][2]}


def test_book():
    ad = bw.Adapter()
    assert ad.parse(BOOK) == [("b", "BTCUSDT", 1794433022399, 1794433022398, 174478070, "74600", "8.454", "74600.1", "7.416")]
    assert ad.parse(BOOK.replace('"snapshot"', '"delta"')) is None
    assert ad.parse(BOOK.replace('"b":[["74600","8.454"]]', '"b":[]')) is None  # пустая сторона — кадр сырым, не выдумываем
    assert ad.parse(BOOK.replace('"seq"', '"newfield":1,"seq"')) is None
    assert ad.symbol(BOOK) == "btcusdt" and ad.lag_ms(BOOK, 1794433022499 * 10**6) == 100


def test_control_frames_raw():
    ad = bw.Adapter()
    for f in ('{"success":true,"ret_msg":"","conn_id":"x","req_id":"","op":"subscribe"}', '{"success":true,"ret_msg":"pong","conn_id":"x","req_id":"","op":"ping"}', "not json"):
        assert ad.parse(f) is None
    assert ad.symbol('{"success":true}') is None and ad.lag_ms('{"success":true}', 1) is None


def test_trades_batch_unique_ordered_keys():
    ad = bw.Adapter()
    frame = TRADE % ",".join([FILL % ("Sell", "5.2", "id-a", "false"), FILL % ("Sell", "675.8", "id-b", "false"), FILL % ("Buy", "4.8", "id-c", "false")])
    recs = ad.parse(frame)
    assert [r[4] for r in recs] == [272596956733000, 272596956733001, 272596956733002]  # пачка с общим seq -> строго растущий ключ
    assert recs[0] == ("t", "XRPUSDT", 1794433203184, 1794433203183, 272596956733000, "1.0634", "5.2", 272596956733000, 272596956733000, True, "5.2")
    assert [r[9] for r in recs] == [True, True, False]  # m: тейкер Sell -> покупатель мейкер
    assert ad.symbol(frame) == "xrpusdt"
    for bad in (FILL % ("Sell", "1", "i", "true"), FILL % ("Hold", "1", "i", "false")):  # блок-сделка / неизвестная сторона — сырым
        assert ad.parse(TRADE % bad) is None
    assert ad.parse(TRADE % "") is None


def test_mark_state_merge_and_skip():
    ad = bw.Adapter()
    assert ad.parse(TICK_DELTA % ('{"symbol":"BTCUSDT","markPrice":"1"}', 1794433021000)) == []  # дельта до снапшота
    assert ad.parse(TICK_SNAP) == [("m", "BTCUSDT", 1794433021421, "74601.07", "", "", "74634.68", "0.00006514", 1794441600000)]
    assert ad.parse(TICK_DELTA % ('{"symbol":"BTCUSDT","ask1Price":"74600.10","ask1Size":"7.377"}', 1794433021521)) == []  # не mark — не пишем
    assert ad.parse(TICK_DELTA % ('{"symbol":"BTCUSDT","markPrice":"74602.5"}', 1794433021621)) == [("m", "BTCUSDT", 1794433021621, "74602.5", "", "", "74634.68", "0.00006514", 1794441600000)]
    assert ad.parse(TICK_DELTA % ('{"symbol":"BTCUSDT","markPrice":"99"}', 1794433021500)) == []  # запоздавшая дельта другого пути не портит состояние
    assert ad.parse(TICK_DELTA % ('{"symbol":"BTCUSDT","indexPrice":"74700"}', 1794433021700))[0][3] == "74602.5"


def test_roundtrip_codec():
    ad = bw.Adapter()
    recs = ad.parse(BOOK) + ad.parse(TRADE % ",".join([FILL % ("Sell", "5.2", "a", "false"), FILL % ("Buy", "4.8", "b", "false")])) + ad.parse(TICK_SNAP)
    enc = codec.Encoder()
    text = "".join(enc.record(1794433022500 * 10**6 + i * 1000, r) for i, r in enumerate(recs))
    got = list(codec.decode(text))
    assert [(g["t"], g["s"]) for g in got] == [(r[0], r[1]) for r in recs]
    assert got[1]["a"] == 272596956733000 and got[2]["a"] == 272596956733001 and got[2]["m"] is False and got[3]["r"] == "0.00006514"


def test_streams_per_conn_stays_under_documented_limit():
    """TK-533: документация Bybit (v5/ws/connect) — linear до 100 args на соединение (TBD); раскладка боевого набора с запасом и подписка одним сообщением в пределах 21 000 знаков."""
    ad = bw.Adapter()
    assert ad.streams_per_conn <= 80 < 100
    plan = ad.plan([f"S{i}USDT" for i in range(389)], ad.kinds, ad.streams_per_conn)
    assert max(len(t) for _, _, t in plan) <= ad.streams_per_conn and len(plan) == 15  # 5 шардов на вид × 3 вида
    assert all(len(m) < 21_000 for _, _, t in plan for m in ad.subscribe_messages(t))
