import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import closed, gate, hist, status, universe


def test_base_of():
    assert universe.base_of("BTCUSDT") == ("BTC", 1)
    assert universe.base_of("1000PEPEUSDT") == ("PEPE", 1000)
    assert universe.base_of("1000000MOGUSDT") == ("MOG", 1000000)
    assert universe.base_of("1INCHUSDT") == ("1INCH", 1)
    assert universe.base_of("1000SATS_USDT") == ("SATS", 1000)
    assert universe.base_of("BTC_USDT") == ("BTC", 1)
    assert universe.base_of("SHIB1000USDT") == ("SHIB", 1000)
    assert universe.base_of("0GUSDT") == ("0G", 1)
    assert universe.base_of("") == ("", 1)


def test_candidates_and_pick():
    c, b, m = universe.candidates("1000PEPEUSDT")
    assert c == ["PEPEUSDT", "1000PEPEUSDT"] and b == "PEPE" and m == 1000
    assert universe.pick(c, {"1000PEPEUSDT"}, set()) == "1000PEPEUSDT"
    assert universe.pick(c, set(), {"PEPEUSDT"}) == "PEPEUSDT"
    assert universe.pick(c, set(), set()) is None


def test_classify():
    assert status.classify(1, 1, 5)[0] == "есть"
    assert status.classify(0, 1, 5)[0] == "есть"
    assert "сборкой" in status.classify(0, 1, 0)[1]
    assert "нет" in status.classify(0, 0, 0)[1]
    assert status.classify(1, 1, 0)[0] == "нет в архиве"


def test_gate_times_and_file(tmp_path):
    import gzip
    assert gate.check_times([0, 60000, 120000]) == []
    assert any("дубль" in b for b in gate.check_times([0, 60000, 60000]))
    assert any("не на минуте" in b for b in gate.check_times([0, 61000]))
    f = tmp_path / "X.csv.gz"
    head = "t_open_ms,o,h,l,c,v,quote_v,n_trades"
    with gzip.open(f, "wt", encoding="ascii", newline="") as g:
        g.write(head + "\n60000,1,2,0.5,1.5,10,15,\n120000,1.5,2,1,1.2,5,6,\n")
    t, rows = gate.read_file(str(f))
    assert t == [60000, 120000] and rows[0] == ["1", "2", "0.5", "1.5", "10", "15"]
    with gzip.open(f, "wt", encoding="ascii", newline="") as g:
        g.write("bad\n")
    try:
        gate.read_file(str(f))
        assert False
    except ValueError:
        pass
    # пустая пара (REST пуст): файл только с шапкой + cov 0 строк — зелёное; строки при cov 0 — красное
    with gzip.open(f, "wt", encoding="ascii", newline="") as g:
        g.write(head + "\n")
    assert gate.check_symbol((str(f), "E", (0, "", ""))) == []
    assert gate.check_symbol((str(f), "E", (5, "1", "2")))[0].startswith("E: файл пуст")
    with gzip.open(f, "wt", encoding="ascii", newline="") as g:
        g.write(head + "\n60000,1,2,0.5,1.5,10,15,\n")
    assert gate.check_symbol((str(f), "X", (1, "60000", "60000"))) == []
    assert "строк 1/2" in gate.check_symbol((str(f), "X", (2, "60000", "60000")))[0]


def test_get_retries_server_timeout(monkeypatch):
    """retCode 10000 (Server Timeout) — повтор; 10001 — пусто без повтора."""
    import io, json
    seq = [{"retCode": 10000, "retMsg": "Server Timeout"}, {"retCode": 0, "result": {"list": [["1"]]}}]
    calls = []

    def fake(url, timeout):
        calls.append(url)
        return io.StringIO(json.dumps(seq[len(calls) - 1]))

    monkeypatch.setattr(hist.urllib.request, "urlopen", fake)
    monkeypatch.setattr(hist.time, "sleep", lambda s: None)
    assert hist.get("x", 1e9) == {"list": [["1"]]} and len(calls) == 2
    seq[:] = [{"retCode": 10001, "retMsg": "Not supported symbols"}]
    calls.clear()
    assert hist.get("x", 1e9) == {"list": []} and len(calls) == 1


def _trades(tmp_path, name, rows):
    import gzip
    f = tmp_path / name
    with gzip.open(f, "wt", encoding="ascii", newline="") as g:
        g.write("id,timestamp,price,volume,side\n")
        for r in rows:
            g.write("%d,%d,%s,%s,%s\n" % r)
    return str(f)


def test_closed_candles_rules(tmp_path):
    """o = c прошлой сделочной минуты (через сутки), первая минута — первая сделка; h/l с учётом o; пустая минута не пишется;
    конец свипа: buy — max цены последней мс, sell — min; неоднозначная мс — ≥ 2 цен."""
    import io
    d1 = _trades(tmp_path, "a.csv.gz", [(1, 60000, "10", "1", "buy"), (2, 60500, "12", "2", "buy"), (3, 61000, "11", "1", "sell")])
    d2 = _trades(tmp_path, "b.csv.gz", [
        (4, 180000, "13", "1", "buy"),                                            # минута 3 (минута 2 без сделок — не пишется), o=11 с d1
        (5, 240001, "14", "1", "buy"), (6, 240001, "15", "1", "buy"),            # buy-свип: c = max = 15
        (7, 300002, "9", "1", "sell"), (8, 300002, "8", "1", "sell"),            # sell-свип: c = min = 8
        (9, 360003, "7", "1", "sell"), (10, 360003, "8", "1", "buy"),            # смешанные стороны: решает сторона max id (buy)
    ])
    st, out = [None], io.StringIO()
    n, tr, amb = closed.candles_day(d1, st, out)
    assert (n, tr, amb) == (1, 3, set()) and st[0] == "11"
    first = out.getvalue().splitlines()[0].split(",")
    assert first[:5] == ["60000", "10", "12", "10", "11"] and first[5] == "4" and first[7] == "3"  # o = первая сделка суток, v = 1+2+1
    out = io.StringIO()
    n, tr, amb = closed.candles_day(d2, st, out)
    rows = [ln.split(",") for ln in out.getvalue().splitlines()]
    assert [r[0] for r in rows] == ["180000", "240000", "300000", "360000"]
    assert rows[0][1:5] == ["11", "13", "11", "13"]                                # o = c d1, h с учётом, l = o
    assert rows[1][1:5] == ["13", "15", "13", "15"] and rows[1][4] == "15"
    assert rows[2][1:5] == ["15", "15", "8", "8"]
    assert rows[3][4] == "8" and amb == {240000, 300000, 360000}


def test_closed_gate_compare():
    import closed_gate as cg
    a = {60000: (1, 2, 1, 2, 3, 4, 5), 120000: (2, 2, 2, 2, 1, 2, 1), 180000: (2, 3, 2, 3, 1, 3, 1)}
    r = {60000: (1, 2, 1, 2.5, 3, 4), 120000: (2.5, 2.5, 2, 2, 1, 2), 180000: (2, 3, 2, 3, 1, 3), 240000: (3, 3, 3, 3, 0, 0)}
    res = cg.compare(a, {60000}, r)
    assert res["bad_c"] == 1 and res["bad_o"] == 1 and res["unexplained"] == 0 and res["rest_only_flat"] == 1 and cg.ok(res)
    assert cg.compare(a, set(), r)["unexplained"] == 2 and not cg.ok(cg.compare(a, set(), r))
