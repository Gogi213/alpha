#!/usr/bin/env python3
"""Гейт TK-461: trade_fast == tk461_sim.trade (legs, m, reason, mfe) побитно на случайных путях; окна 10·tf минут (tf 60/240/1440)."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_sim as S
from tk461_fast import trade_fast


def path(rng, n, vol):
    c = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    return o, h, l, c


def main():
    rng = np.random.default_rng(7)
    bad = tot = 0
    for tf in (60, 240, 1440):
        n = 10 * tf + 5
        for it in range(300 if tf < 1440 else 120):
            vol = rng.choice([0.0005, 0.001, 0.002])
            o, h, l, c = path(rng, n, vol)
            e = int(rng.integers(0, 3)); last = n - 2
            ep = o[e]; atr = ep * vol * rng.uniform(5, 30)
            m = rng.choice([0.5, 1, 2]); r = rng.choice([0.5, 1, 2])
            dist = m * atr
            forms = [dict(stop=-np.inf), dict(stop=ep - dist), dict(stop=ep - dist, tgt=ep + r * dist),
                     dict(stop=ep - dist, trail=r * atr), dict(stop=-np.inf, trail=r * atr),
                     dict(stop=ep - dist, part=(ep + dist, 0.5, atr)), dict(stop=ep - dist, be=ep + r * dist),
                     dict(stop=ep - dist, tgt=ep + 2 * dist, be=ep + dist), dict(stop=ep - dist, trail=atr, be=ep + dist),
                     dict(stop=ep - dist, tgt=ep + r * dist, trail=r * atr)]
            for kw in forms:
                a = S.trade(o, h, l, c, e, last, ep, bar_end=(), **kw)
                b = trade_fast(o, h, l, c, e, last, ep, bar_end=(), **kw)
                tot += 1
                if repr(a) != repr(b):
                    bad += 1
                    if bad < 5:
                        print("DIFF", tf, kw, a, b)
    print("cases", tot, "diff", bad)
    sys.exit(1 if bad else 0)


main()
