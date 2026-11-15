"""Тесты bx_trades.py на синтетике (запуск: python -m pytest tools/arb_stats/test_bx_trades.py или python test_bx_trades.py)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bx_trades as bx  # noqa: E402

T0 = 1_000_000_000_000
T1 = T0 + 3_600_000


def book(rows):
    a = np.array(rows, float)
    return {"e": a[:, 0].astype(np.int64), "bid": a[:, 1], "ask": a[:, 2]}


def trades(rows):
    """rows: (T, price, side +1 покупатель-агрессор / −1 продавец, qty)."""
    a = np.array(rows, float)
    return {"T": a[:, 0].astype(np.int64), "p": a[:, 1], "praw": a[:, 1], "q": a[:, 3], "m": np.where(a[:, 2] > 0, 0, 1).astype(np.int8)}


def test_batches_and_freq():
    B = bx.make_batches(trades([(T0 + 10, 100.2, 1, 1), (T0 + 10, 100.3, 1, 2), (T0 + 10, 100.0, -1, 1), (T0 + 5000, 100.1, -1, 1), (T0 + 5000, 100.0, -1, 3)]))
    assert list(B["t"]) == [T0 + 10, T0 + 10, T0 + 5000]
    assert list(B["side"]) == [-1, 1, -1] or list(B["side"]) == [1, -1, -1]
    b = {s: i for i, s in enumerate(B["side"])}
    buy = [i for i in range(3) if B["side"][i] > 0][0]
    assert B["first"][buy] == 100.2 and B["last"][buy] == 100.3 and B["n"][buy] == 2 and abs(B["usd"][buy] - (100.2 + 200.6)) < 1e-9
    sell = [i for i in range(3) if B["side"][i] < 0 and B["t"][i] == T0 + 5000][0]
    assert B["first"][sell] == 100.1 and B["last"][sell] == 100.0
    f = bx.freq_stats(B, 5, T0, T0 + 10_000)
    assert f["n_times"] == 2 and f["gap_med"] == 4990 and abs(f["stale"][2] - (4990 - 10_000 + 5000 + 10 - 0) / 10_000) < 1.0
    assert abs(f["stale"][3] - 0) < 1e-12


def test_lead_lag_response():
    # F: середина прыгает на 1 % в T0+10 с; BingX-снимок остаётся старым; сделки BingX — на старом уровне, потом (через 3 с) на новом
    F = book([(T0, 99.95, 100.05)] + [(T0 + 10_000, 100.95, 101.05)])
    X = book([(T0, 100.0, 100.1)])
    B = bx.make_batches(trades([(T0 + 5000, 100.1, 1, 1), (T0 + 11_000, 100.1, 1, 1), (T0 + 13_000, 101.1, 1, 1), (T0 + 20_000, 100.9, -1, 1)]))
    G = bx.trade_gaps(F, B, 0, np.zeros(10), X)
    out = bx.lead_lag(F, X, B, G, T0, T1 + 3_600_000, xs=(0.005,))[0.005]
    assert len(out) == 1
    t, d, led, lag_t, lag_s, spread, m, nb, have = out[0]
    assert t == T0 + 10_000 and d == 1 and led == 0 and lag_t == 3000 and np.isnan(lag_s) and have == 1


def test_lead_lag_led():
    F = book([(T0, 99.95, 100.05), (T0 + 10_000, 100.95, 101.05)])
    X = book([(T0, 100.0, 100.1), (T0 + 9500, 101.0, 101.1)])
    B = bx.make_batches(trades([(T0 + 9800, 101.1, 1, 1)]))
    G = bx.trade_gaps(F, B, 0, np.zeros(10), X)
    out = bx.lead_lag(F, X, B, G, T0, T1 + 3_600_000, xs=(0.005,))[0.005]
    assert len(out) == 1 and out[0][2] == 1.0


def test_lead_lag_snapshot_response():
    # BingX-снимок догоняет через 300 мс
    F = book([(T0, 99.95, 100.05), (T0 + 10_000, 100.95, 101.05)])
    X = book([(T0, 100.0, 100.1), (T0 + 10_300, 101.0, 101.1)])
    B = bx.make_batches(trades([(T0 + 5000, 100.1, 1, 1)]))
    G = bx.trade_gaps(F, B, 0, np.zeros(10), X)
    out = bx.lead_lag(F, X, B, G, T0, T1 + 3_600_000, xs=(0.005,))[0.005]
    assert len(out) == 1 and out[0][4] == 300 and np.isnan(out[0][3])


def test_trade_divergence_and_snapshot_classes():
    # F bid 101, ask 101.1; BingX торгует по 100.0 (продажа в BingX дешевле) → buy-пачка по 100,0: Sell = +100 bps
    F = book([(T0, 101.0, 101.1), (T0 + 20_000, 100.0, 100.1)])
    X = book([(T0, 99.9, 100.0)])
    B = bx.make_batches(trades([(T0 + 5000, 100.0, 1, 2), (T0 + 15_000, 100.0, 1, 1), (T0 + 30_000, 100.0, -1, 1)]))
    cen = np.zeros(1000)
    G = bx.trade_gaps(F, B, 0, cen * 0, X)   # центр 0
    G["c"] = np.zeros(len(B["t"]))
    r = bx.trade_divergence(F, B, G, T0, T1, "0", thetas=(50.0,))
    e = r["eps"][(50.0, 1)]
    assert len(e) == 2                     # две подходящие пачки с паузой 10 с (> 5 с) — два эпизода
    assert abs(e[0][1] - 100.0) < 1e-9
    assert e[0][2] == 15_000 and e[0][3] == 25_000   # F сошлась в T0+20 с (15 с после пачки); следующая пачка с Sell < θ — в T0+30 с
    assert e[1][2] == 5000
    assert r["q"][(50.0, 1)][0].sum() == 2 and r["q"][(50.0, -1)][0].sum() == 0
    # снимки: BingX-ask = 100,0, F bid 101 ⇒ Sell = 100 bps ≥ 50 с T0 до T0+20 с (F падает) — один эпизод, решающая первая пачка — покупатель по 100,0 с Sell ≥ θ ⇒ подтверждён
    T, S, Bu, ia, ib = None, None, None, None, None
    import core
    T, S, Bu, ia, ib = core.pair_series(F, X, T1)
    sd = bx.snapshot_divergence(T, S, Bu, 0, np.zeros(1000), B, G, T0, T1, "0", thetas=(50.0,))
    runs = sd[(50.0, 1)]["runs"]
    assert len(runs) == 1 and runs[0][1] == 20_000 and runs[0][2] == 0


def test_snapshot_contradicted():
    # снимок BingX устарел: ask 100,0, F bid 101 (Sell +100 bps) 20 с; но BingX торгует выше: покупка по 101,0 ⇒ Sell = 0 < θ ⇒ опровергнут
    F = book([(T0, 101.0, 101.1)])
    X = book([(T0, 99.9, 100.0)])
    B = bx.make_batches(trades([(T0 + 5000, 101.0, 1, 1)]))
    G = bx.trade_gaps(F, B, 0, np.zeros(10), X)
    import core
    T, S, Bu, ia, ib = core.pair_series(F, X, T0 + 20_000)
    sd = bx.snapshot_divergence(T, S, Bu, 0, np.zeros(10), B, G, T0, T0 + 20_000, "0", thetas=(50.0,))
    runs = sd[(50.0, 1)]["runs"]
    assert len(runs) == 1 and runs[0][2] == 1


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
