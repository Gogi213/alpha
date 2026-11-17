import sys, pickle, json, numpy as np, pandas as pd
sys.path.insert(0, "/opt/arb-hold2/tools/arb_stats")
import maker_run
from hold_common import build_coins, unit_of
cfg = json.load(open("/root/rev534/syms.json")); t0, t1 = cfg["t0"], cfg["t1"]
B = pickle.load(open("/root/rev534/my_books.pkl","rb"))["books"]
coins, _ = build_coins("/data/arb-hold/b2/parsed", ["2026-11-13","2026-11-14","2026-11-15","2026-11-16"])
tab = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb"))
print("BARD pairs in exec table:", [k for k in tab["pairs"] if k[0]=="BARD"], " skipped:", [s for s in tab["skipped"] if s[0] in ("BARD","1INCH","AKE")])
for c in ("BARD","1INCH"):
    for ex in ("binance","okx","bybit"):
        sym, mult, meta = coins[c][ex]
        a = maker_run.load_book("/data/arb-hold/b2/parsed", ex, sym, mult, unit_of(ex, meta))
        e = a["e"]; w = (e>=t0)&(e<t1)
        me = B[(c,ex)][0]; mw = (me>=t0)&(me<t1)
        print(c, ex, sym, "exec recs", int(w.sum()), "mine", int(mw.sum()), " exec first/last", e[w][:1], e[w][-1:], " mine", me[mw][:1], me[mw][-1:])
        # largest gaps
        ge = np.diff(e[w]); gm = np.diff(me[mw])
        print("   gaps>5s exec", int((ge>5000).sum()), "mine", int((gm>5000).sum()), " gaps>1h exec", int((ge>3.6e6).sum()), "mine", int((gm>3.6e6).sum()))
