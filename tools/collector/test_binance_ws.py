import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import binance_ws as bw  # noqa: E402


def sym(name, status="TRADING", ctype="PERPETUAL", quote="USDT"):
    return {"symbol": name, "status": status, "contractType": ctype, "quoteAsset": quote}


def tick(name, qv):
    return {"symbol": name, "quoteVolume": qv}


def test_pool_excludes_top_and_threshold():
    names = [f"A{i:02d}USDT" for i in range(15)]
    syms = [sym(n) for n in names]
    tks = [tick(n, str(1_000_000 * (20 - i))) for i, n in enumerate(names)]
    tks[14] = tick(names[14], "300000")  # ровно порог — входит
    pool, top = bw.select_pool(syms, tks)
    assert top == names[:10]
    assert pool == sorted(names[10:])
    tks[14] = tick(names[14], "299999.99")
    pool, _ = bw.select_pool(syms, tks)
    assert names[14] not in pool


def test_pool_filters_bad_rows():
    syms = [sym("OKUSDT"), sym("QTRUSDT", ctype="CURRENT_QUARTER"), sym("BUSDUSD", quote="USD"),
            sym("SETUSDT", status="SETTLING"), sym("ZEROUSDT"), sym("EMPTYUSDT"), sym("NEGUSDT"), sym("NOTICKUSDT")]
    tks = [tick("OKUSDT", "5000000"), tick("QTRUSDT", "9e9"), tick("BUSDUSD", "9e9"), tick("SETUSDT", "9e9"),
           tick("ZEROUSDT", "0"), tick("EMPTYUSDT", ""), tick("NEGUSDT", "-5")]
    pool, top = bw.select_pool(syms, tks, top_exclude=0)
    assert pool == ["OKUSDT"] and top == []


def test_pool_small():
    pool, top = bw.select_pool([sym("AUSDT")], [tick("AUSDT", "9000000")])
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


def test_day_path_boundary():
    # 2026-11-11 23:59:59.999 и 2026-11-12 00:00:00 UTC — разные файлы и каталоги суток
    p1, d1 = bw.day_path("/r", "book00", 1, 1794441599.999)
    p2, d2 = bw.day_path("/r", "book00", 1, 1794441600.0)
    assert (d1, d2) == ("2026-11-11", "2026-11-12")
    assert p1.endswith("book00-1.tsv.zst") and p1 != p2


def test_mark_shard_single_stream():
    assert bw.shard_streams(["AUSDT"], "mark", 64) == [("mark00", ["!markPrice@arr@1s"])]


BOOK = {"e": "bookTicker", "u": 12081389761850, "s": "AVAXUSDT", "ps": "AVAXUSDT", "b": "8.6790", "B": "42", "a": "8.6800", "A": "285", "T": 1794418695170, "E": 1794418695171, "st": 1}
TRADE = {"e": "aggTrade", "E": 1794418694042, "a": 92150638, "s": "AIOTUSDT", "p": "0.1742400", "q": "410", "nq": "410", "f": 180333636, "l": 180333637, "T": 1794418693917, "m": True, "st": 1}
MARK = {"e": "markPriceUpdate", "E": 1794418695000, "s": "AVAXUSDT", "p": "8.68001", "ap": "8.68002", "P": "8.67", "st": 1, "i": "8.6801", "r": "0.00010000", "T": 1794441600000}


def frame(stream, data):
    return json.dumps({"stream": stream, "data": data}, separators=(",", ":"))


def enc_all(frames, rxs):
    e = bw.Encoder()
    out = []
    for rx, fr in zip(rxs, frames):
        out += e.frame(rx, fr)
    return "".join(out)


def test_roundtrip_book_trade_mark_exact():
    rx0 = 1794418695434654672
    b2 = dict(BOOK, u=BOOK["u"] + 66, B="47")
    t2 = dict(TRADE, a=TRADE["a"] + 1, f=TRADE["l"] + 1, l=TRADE["l"] + 3, m=False)
    frames = [frame("avaxusdt@bookTicker", BOOK), frame("avaxusdt@bookTicker", b2), frame("aiotusdt@aggTrade", TRADE),
              frame("aiotusdt@aggTrade", t2), frame("!markPrice@arr@1s", [MARK, dict(MARK, s="AIOTUSDT")])]
    rxs = [rx0, rx0 + 43_000, rx0 + 50_000, rx0 + 51_000, rx0 + 999_999_999]
    text = enc_all(frames, rxs)
    recs = list(bw.decode(text))
    assert [r["t"] for r in recs] == ["b", "b", "t", "t", "m", "m"]
    assert recs[0] == {"t": "b", "rx_ns": rx0, "E": BOOK["E"], "T": BOOK["T"], "s": "AVAXUSDT", "u": BOOK["u"], "b": "8.6790", "B": "42", "a": "8.6800", "A": "285"}
    assert recs[1]["u"] == b2["u"] and recs[1]["rx_ns"] == rxs[1] and recs[1]["B"] == "47"
    assert (recs[2]["a"], recs[2]["f"], recs[2]["l"], recs[2]["m"]) == (TRADE["a"], TRADE["f"], TRADE["l"], True)
    assert (recs[3]["a"], recs[3]["f"], recs[3]["l"], recs[3]["m"]) == (t2["a"], t2["f"], t2["l"], False)
    assert recs[4]["s"] == "AVAXUSDT" and recs[5]["s"] == "AIOTUSDT" and recs[4]["T"] == MARK["T"] and recs[4]["r"] == "0.00010000" and recs[4]["ap"] == "8.68002"
    assert recs[4]["rx_ns"] == recs[5]["rx_ns"] == rxs[4]


def test_unexpected_frames_go_raw_unchanged():
    rx = 1794418695434654672
    cases = ['{"result":null,"id":1}', "не json", "", '{"stream":"x@bookTicker"}', '{"stream":"x","data":5}',
             frame("a@bookTicker", dict(BOOK, new_field=1)), frame("a@bookTicker", dict(BOOK, ps="OTHER")),
             frame("a@aggTrade", dict(TRADE, nq="999")), frame("a@aggTrade", dict(TRADE, st=2)),
             frame("!markPrice@arr@1s", []), frame("!markPrice@arr@1s", [dict(MARK, x=1)])]
    for c in cases:
        recs = list(bw.decode(enc_all([c], [rx])))
        assert recs == [{"t": "r", "rx_ns": rx, "frame": c}], c


def test_ping_and_control_records():
    e = bw.Encoder()
    text = "".join(e.control(100, "connected", "wss://x") + e.ping(5_000_100, 351_234) + e.control(9_000_000_000, "closed"))
    assert list(bw.decode(text)) == [
        {"t": "c", "rx_ns": 100, "what": "connected", "detail": "wss://x"},
        {"t": "g", "rx_ns": 5_000_100, "rtt_us": 351_234},
        {"t": "c", "rx_ns": 9_000_000_000, "what": "closed", "detail": ""}]


def test_day_writer_cuts_days_and_marks_ok(tmp_path):
    zstandard = pytest.importorskip("zstandard")
    w = bw.DayWriter(str(tmp_path), "book00", 1)
    d1 = 1794441599_900_000_000  # 2026-11-11 23:59:59.9 UTC
    d2 = 1794441600_100_000_000  # 2026-11-12 00:00:00.1 UTC
    w.write(d1, lambda e, rx: e.frame(rx, frame("avaxusdt@bookTicker", BOOK)), sample=True)
    w.write(d2, lambda e, rx: e.frame(rx, frame("avaxusdt@bookTicker", dict(BOOK, u=BOOK["u"] + 1))))
    w.close()
    f1, f2 = tmp_path / "2026-11-11" / "book00-1.tsv.zst", tmp_path / "2026-11-12" / "book00-1.tsv.zst"
    assert f1.exists() and f2.exists()
    assert (tmp_path / "2026-11-11" / "book00-1.tsv.zst.ok").read_text() == "1\n"
    # новые сутки кодируются с нуля: первая запись суток — абсолютные u и приём
    r2 = list(bw.decode(zstandard.ZstdDecompressor().stream_reader(f2.open("rb"), read_across_frames=True).read().decode()))
    assert r2[0]["u"] == BOOK["u"] + 1 and r2[0]["rx_ns"] == d2
    assert w.samples == [] or w.samples == [d1]  # выборка отставания отдачи не теряется при смене суток


def test_flush_makes_data_readable_midfile(tmp_path):
    zstandard = pytest.importorskip("zstandard")
    w = bw.DayWriter(str(tmp_path), "trade00", 2)
    rx = 1794418694269720375
    w.write(rx, lambda e, r: e.frame(r, frame("aiotusdt@aggTrade", TRADE)), sample=True)
    w.flush()  # кадр на диске, файл ещё открыт (A8)
    path = next((tmp_path).rglob("trade00-2.tsv.zst"))
    data = zstandard.ZstdDecompressor().stream_reader(path.open("rb"), read_across_frames=True).read().decode()
    assert [r["t"] for r in bw.decode(data)] == ["t"]
    assert w.out_lags and w.out_lags[0][1] >= 0  # лаг отдачи записан
    w.close()


def test_aggregate_health_schema_and_pool_filter():
    now = 1_794_500_000_000
    f = {"shard": "book00", "ts_ms": now, "started_ms": now - 5000, "with_data": ["avaxusdt", "btcusdt"], "connected": 1, "ping_ms": [300.0, 340.0],
         "lag_in": [200.0, 230.0], "lag_out": [400.0], "timeouts": [now - 10, now - 2 * 3600_000], "reconnects": [], "last_frame_ms": now - 5, "last_write_ms": now - 500}
    h = bw.aggregate([f], now, ["AVAXUSDT", "ETHUSDT"])
    assert h["schema"] == 1 and h["exchange"] == "binance"
    assert h["symbols"] == {"subscribed": 2, "with_data_1m": 1}  # btcusdt вне пула не считается
    assert h["ws"]["timeouts_1h"] == 1 and h["ws"]["timeouts_24h"] == 2 and h["ws"]["reconnects_1h"] == 0
    assert h["ws"]["ping_ms"]["max"] == 340.0 and h["lag_in_ms"]["window_s"] == 60 and h["lag_out_ms"]["p50"] == 400.0
    h0 = bw.aggregate([], now, ["AVAXUSDT"])
    assert h0["lag_in_ms"]["p50"] is None and h0["ws"]["ping_ms"]["max"] is None and h0["symbols"]["with_data_1m"] == 0


def test_percentile():
    assert bw.percentile([], 0.5) is None
    assert bw.percentile([5], 0.99) == 5
    assert bw.percentile(list(range(100)), 0.99) == 99


def test_pinger_aborts_dead_connection(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(bw, "PING_PERIOD_S", 0.01)
    monkeypatch.setattr(bw, "PING_TIMEOUT_S", 0.05)
    shard = bw.Shard(str(tmp_path), "book00", "public", ["btcusdt@bookTicker"])
    aborted = []

    class FakeWs:
        transport = type("T", (), {"abort": lambda self: aborted.append(1)})()

        async def ping(self):
            return asyncio.get_running_loop().create_future()  # pong не придёт

    asyncio.run(asyncio.wait_for(shard.pinger(FakeWs(), None, 1), 2))
    assert aborted == [1]
    assert len(shard.timeouts) == 1
    kinds = [json.loads(ln)["ev"] for ln in open(shard.events_path)]
    assert kinds == ["timeout", "ping_reconnect"]


def test_frame_lag_and_path_verdict():
    rx_ns = 1_000_000_000_000 * 1_000_000
    frame = json.dumps({"stream": "btcusdt@bookTicker", "data": {"E": 1_000_000_000_000 - 250}})
    assert bw.frame_lag(frame, rx_ns) == pytest.approx(250)
    assert bw.frame_lag(json.dumps({"data": [{"E": 1_000_000_000_000 - 40}]}), rx_ns) == pytest.approx(40)
    for bad in ("", "не json", "{}", '{"data": []}', '{"data": {"x": 1}}'):
        assert bw.frame_lag(bad, rx_ns) is None
    assert bw.path_verdict([]) and bw.path_verdict([9e9] * (bw.PROBE_MIN_SAMPLES - 1))  # мало выборок — не отбраковываем
    assert bw.path_verdict([250.0] * 100)
    assert not bw.path_verdict([600.0] * 100)  # p50 выше порога
    assert not bw.path_verdict([250.0] * 90 + [2000.0] * 10)  # p99 выше порога
    assert bw.path_verdict([250.0] * 199 + [2000.0])  # одиночный всплеск (0,5 %) — путь чистый
