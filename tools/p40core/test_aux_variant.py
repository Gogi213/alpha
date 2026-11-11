import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aux_variant as av


def test_chunk_days():
    d = av.chunk_days("/root/p40core/out_d1v4/100/2026-09-17+14/1316.k1p60")
    assert len(d) == 14 and d[0] == "2026-09-17" and d[-1] == "2026-09-30"
    assert av.chunk_days("/o/5/2026-09-30+3/9.k1m60") == ["2026-09-30", "2026-10-01", "2026-10-02"]


def test_aux_files_and_roots():
    f = av.aux_files({("binance", "BTCUSDT"), ("bybit", "BTCUSDT")}, ["2026-09-17", "2026-09-18"])
    assert len(f) == 2 * 24 * 3 and all(x.startswith("chft/binance_futures/2026-09-1") for x in f)
    assert "chft/binance_futures/2026-09-17/05/BTCUSDT_open_interest.parquet" in f
    assert av.roots("bybit", ["2026-09-17"], "/r") == "-"
    assert av.roots("okx", ["2026-09-17", "2026-09-18"], "/r") == "/r/chft/okx_futures/2026-09-17,/r/chft/okx_futures/2026-09-18"
