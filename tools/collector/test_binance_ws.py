import os
import sys

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


def test_hour_path_boundary():
    # 2026-11-11 23:59:59.999 и 2026-11-12 00:00:00 UTC — разные файлы и каталоги
    p1, h1, d1 = bw.hour_path("/r", "book00", 1, 1794441599.999)
    p2, h2, d2 = bw.hour_path("/r", "book00", 1, 1794441600.0)
    assert (d1, h1) == ("2026-11-11", 23) and (d2, h2) == ("2026-11-12", 0)
    assert p1.endswith("book00-1-23.jsonl.zst") and p1 != p2


def test_percentile():
    assert bw.percentile([], 0.5) is None
    assert bw.percentile([5], 0.99) == 5
    assert bw.percentile(list(range(100)), 0.99) == 99


def test_writer_roundtrip(tmp_path):
    import zstandard

    w = bw.HourWriter(str(tmp_path), "book00", 1)
    w.write(1794441599_000_000_000, '{"stream":"x","data":{}}')
    w.write(1794441601_000_000_000, b'{"stream":"y","data":{}}')  # другой час — новый файл
    w.close()
    files = sorted(str(p) for p in tmp_path.rglob("*.zst"))
    assert len(files) == 2
    with open(files[0], "rb") as f:
        txt = zstandard.ZstdDecompressor().stream_reader(f).read()
    assert txt == b'1794441599000000000 {"stream":"x","data":{}}\n'
