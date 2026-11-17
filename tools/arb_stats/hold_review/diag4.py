import sys, pickle, json, numpy as np, pandas as pd
sys.path.insert(0, "/opt/arb-hold2/tools/arb_stats")
import maker_run
from hold_common import build_coins, unit_of
cfg = json.load(open("/root/rev534/syms.json")); coins = cfg["coins"]; t0, t1 = cfg["t0"], cfg["t1"]
df = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb"))["df"]
X = df[(df.view=="z")&(df.th==50)&df.coin.isin(coins)]
E = pd.read_pickle("/root/rev534/my_eps.pkl")
B = pickle.load(open("/root/rev534/my_books.pkl","rb"))["books"]
b = E.merge(X, on=["coin","exA","exB","side","t"], suffixes=("","_x"))
b["my_stale"] = (b.silA>5000)|(b.silB>5000)
oth = b[["f_gap","f_contract","f_tradfi","f_delist","f_funding","f_mark"]].any(axis=1)
b["keep_fix"] = ~b.my_stale & ~oth
print("matched episodes, exec contract/gap/... kept, stale from raw (mine):")
for lab, ms in (("5s",5000),("60s",60000),("10min",600000)):
    s = b[b.dur>=ms]; print(f">= {lab}: exec keep {int(s.keep.sum())} -> fixed keep {int(s.keep_fix.sum())} (exec stale-layer {int((s.layer=='stale').sum())}, raw-stale {int((s.my_stale & ~s[['f_gap']].any(axis=1)).sum())})")
DAY=86400000; TZ=4*3600000
s = b[(b.dur>=60000)&b.keep_fix]
print("fixed keep >=60s by pair:", s.groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(12).to_dict())
s10 = b[(b.dur>=600000)&b.keep_fix]
print("fixed keep >=10min:", s10.groupby(["coin","exA","exB"]).agg(n=("dur","size"), maxs=("dur", lambda x: round(x.max()/1000)), size=("size0","median")).sort_values("n",ascending=False).head(12).to_string())
# coverage parsed vs raw (minutes with >=1 record)
coinsmap, _ = build_coins("/data/arb-hold/b2/parsed", ["2026-11-13","2026-11-14","2026-11-15","2026-11-16"])
rows=[]
for c in coins:
    for ex in ("binance","bybit","okx","bingx","gate"):
        if (c,ex) not in B or c not in coinsmap or ex not in coinsmap[c]: continue
        sym, mult, meta = coinsmap[c][ex]
        a = maker_run.load_book("/data/arb-hold/b2/parsed", ex, sym, mult, unit_of(ex, meta))
        if a is None: continue
        me = B[(c,ex)][0]; me = me[(me>=t0)&(me<t1)]; pe = a["e"][(a["e"]>=t0)&(a["e"]<t1)]
        mm = set((me//60000).tolist()); pm = set((pe//60000).tolist())
        rows.append((c, ex, len(mm), len(mm-pm), round(len(mm-pm)/max(len(mm),1),3)))
C = pd.DataFrame(rows, columns=["coin","ex","raw_min","missing_min","share"])
print("symbols", len(C), " with >5% raw minutes absent in parsed:", int((C.share>0.05).sum()), " total share missing:", round(C.missing_min.sum()/C.raw_min.sum(),3))
print(C.sort_values("share", ascending=False).head(12).to_string())
