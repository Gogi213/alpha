import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "collector_health"))
import analyze  # noqa: E402
import arb_api  # noqa: E402
import arb_live  # noqa: E402
import core  # noqa: E402


def det_of(cnt, base):
    n = 4
    st = core.ep_table_stats([(0, 1, 2000, False, 20.0 + base, 30.0, 1.0), (0, 1, 4000, False, 40.0 + base, 20.0, 1.0)])
    return {"cnt": cnt, "st": st, "S": [base * 10 + i for i in range(n)], "B": [-base * 10 - i for i in range(n)], "C": [None, 0, 0, 5]}


def test_outputs_roundtrip_through_api(tmp_path):
    det = {("BBB", "bybit", "okx"): det_of([0, 1, 2, 3], 2), ("AAA", "binance", "bybit"): det_of([1, 4, 6, 24], 1)}
    n = arb_live.write_outputs(tmp_path, 111, 1000, 2000, det)
    assert n == 2
    api = arb_api.Arb(tmp_path)
    doc = json.loads(api.table())
    assert [r["c"] for r in doc["rows"]] == ["AAA", "BBB"] and doc["rows"][0]["cnt"] == [1, 4, 6, 24] and doc["rows"][0]["pk"] == [31.0, 40.0, 41.0]
    assert doc["rows"][1]["net"][0] == 25.0 - core.FEE_RT_BPS    # круги 30 и 20 → медиана 25
    ch = json.loads(api.chart("c=BBB&a=bybit&b=okx"))
    assert ch["S"] == [20, 21, 22, 23] and ch["B"] == [-20, -21, -22, -23] and ch["C"] == [None, 0, 0, 5] and ch["t0"] == 1000 and ch["slot"] == 180
    assert json.loads(api.chart("c=AAA&a=binance&b=bybit"))["S"][0] == 10      # смещения строк верны для каждой пары
    assert api.chart("c=NOPE&a=binance&b=bybit") is None


def test_pending_without_file(tmp_path):
    assert json.loads(arb_api.Arb(tmp_path / "nope").table()) == {"pending": True}
    assert arb_api.Arb(tmp_path / "nope").chart("c=A&a=b&b=c") is None


def test_hook_counts_windows_and_skips_pair_without_centre():
    t1 = 10 * 3600_000
    analyze.G.update({"t0": 0, "t1": t1})
    eps = {core.FEE_RT_BPS: [(t1 - 30 * 60_000, 1, 1000, False, 15.0, 20.0, 1.0), (t1 - 3 * 3600_000, 1, 1000, False, 25.0, None, 1.0),
                              (t1 - 5 * 3600_000, -1, 3000, True, 35.0, None, 1.0), (t1 - 9 * 3600_000, 1, 500, False, 18.0, 17.0, 1.0)]}
    mins = np.arange(0, 600)
    z = np.zeros((4, 600))
    bars = {"m0": 0, "mins": mins, "S": z, "B": z, "centre_m0": 0, "centre": np.zeros(600)}
    d = arb_live.hook({}, bars, eps, {})
    assert d["cnt"] == [1, 2, 3, 4] and d["st"]["n"] == 4 and len(d["S"]) == 200
    assert arb_live.hook({}, bars, {}, {}) is None
