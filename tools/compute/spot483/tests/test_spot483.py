import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gate, status, universe


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
