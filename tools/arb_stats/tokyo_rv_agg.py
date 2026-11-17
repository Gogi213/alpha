"""Проверяющий arb-tokyo: концентрация и разбивка по парам бирж из tokyo.pkl (группа без BingX)."""
import pickle, sys
from collections import defaultdict
import numpy as np
d = pickle.load(open(sys.argv[1], "rb"))
R = d["rows"]
def sel(mode, th, lat, ex_, dm):
    out = {}
    for k, st in R.items():
        if k[0] != mode: continue
        coin, a, b, cfg = k[1], k[2], k[3], k[4]
        if "bingx" in (a, b): continue
        if cfg[0] == th and cfg[1] == lat and cfg[2] == ex_ and cfg[3] == dm:
            out[(coin, a, b)] = st
    return out
for mode, ex_, dm, th in (("tt", "x1-t", "-", 20.0), ("tt", "x1-t", "-", 30.0), ("tt", "c-t", "-", 30.0), ("mk", "x1-t", "L", 30.0), ("mk", "x1-t", "L", 20.0)):
    print(f"## {mode} {ex_} D={dm} th={th/100}%")
    for lat in (1, 2, 5, 10):
        s = sel(mode, th, lat, ex_, dm)
        v = {k: st["usd_cap"] for k, st in s.items()}
        tot = sum(v.values())
        srt = sorted(v.items(), key=lambda kv: -kv[1])
        bycoin = defaultdict(float)
        for (c, a, b), x in v.items(): bycoin[c] += x
        cs = sorted(bycoin.items(), key=lambda kv: -kv[1])
        byex = defaultdict(float)
        for (c, a, b), x in v.items(): byex[f"{a}-{b}"] += x
        pos = sum(1 for x in v.values() if x > 0)
        top = lambda n: sum(x for _, x in srt[:n])
        print(f"L={lat}: total {tot:+.0f} pairs {len(v)} pos {pos}; top1 {top(1):+.0f} top3 {top(3):+.0f} top10 {top(10):+.0f}; "
              f"minus top3 coins {tot - sum(x for _, x in cs[:3]):+.0f} (top coins {[(c, round(x)) for c, x in cs[:3]]}); "
              f"sum positive {sum(x for x in v.values() if x > 0):+.0f} negative {sum(x for x in v.values() if x < 0):+.0f}; by ex {dict((k, round(x)) for k, x in byex.items())}")
        dd = np.array([st["usd_cap_d"] for st in s.values()]).sum(0)
        print("   per day", np.round(dd).tolist(), " per day minus top3 coins", np.round(dd - np.array([s[k]["usd_cap_d"] for k in s if k[0] in dict(cs[:3])]).sum(0)).tolist())
