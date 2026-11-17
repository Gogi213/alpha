"""review-2: are the 'repeats' separate divergences or flicker of one? gaps between consecutive survivor episodes (z50, strict, raw reading, >= 5 s) of a pair+side; recount pairs table after merging episodes closer than G."""
import pickle, numpy as np, pandas as pd
tab = pickle.load(open("/data/arb-hold/v4/flag-strict.pkl", "rb")); df = tab["df"]; TZ = 4*3600_000
z = df[(df.th == 50) & (df.view == "z")]
k = z[(z.dur >= 5000) & z.keep].sort_values(["coin", "exA", "exB", "side", "t"]).copy()
k["end"] = k.t + k.dur
k["gap"] = k.groupby(["coin", "exA", "exB", "side"]).t.shift(-0) - k.groupby(["coin", "exA", "exB", "side"]).end.shift(1)
g = k.gap.dropna() / 1000
print("gap to previous survivor episode (same pair+side), s: q", g.quantile([.1, .25, .5, .75, .9]).round(1).to_dict(), " <10s", round((g < 10).mean(), 3), "<60s", round((g < 60).mean(), 3), "<600s", round((g < 600).mean(), 3))
# any episode (all lengths, all flags) between: how much of the gap is still >= theta? use all z50 episodes >=1s of pair+side
for G in (10, 60, 600):
    newd = ~(k.gap < G * 1000)
    k["div"] = newd.cumsum()
    dv = k.groupby("div").agg(coin=("coin", "first"), exA=("exA", "first"), exB=("exB", "first"), t=("t", "first"))
    dv["day"] = (dv.t + TZ) // 86400000
    pp = dv.groupby(["coin", "exA", "exB"]).agg(n=("t", "size"), days=("day", "nunique"))
    pdd = dv.groupby(["coin", "exA", "exB", "day"]).size()
    print(f"merge gaps < {G}s: divergences {len(dv)}; pairs {len(pp)} by count {pd.cut(pp.n, [0,1,5,20,100,1e9]).value_counts(sort=False).tolist()} days>=2 {int((pp.days>=2).sum())}; pair-days {len(pdd)} by count {pd.cut(pdd, [0,1,5,20,1e9]).value_counts(sort=False).tolist()}")
# >= 60 s
k6 = z[(z.dur >= 60000) & z.keep].sort_values(["coin","exA","exB","side","t"]).copy(); k6["end"]=k6.t+k6.dur
k6["gap"] = k6.t - k6.groupby(["coin","exA","exB","side"]).end.shift(1)
print(">=60s gaps s q", (k6.gap.dropna()/1000).quantile([.1,.5,.9]).round(0).to_dict())
# tolerant variant table for comparison
tt = pickle.load(open("/data/arb-hold/v4/flag-tol.pkl", "rb"))["df"]; tz = tt[(tt.th==50)&(tt.view=="z")&(tt.dur>=5000)&tt.keep].copy(); tz["day"]=(tz.t+TZ)//86400000
pp = tz.groupby(["coin","exA","exB"]).agg(n=("t","size"), days=("day","nunique"))
print("tolerant: pairs", len(pp), pd.cut(pp.n,[0,1,5,20,100,1e9]).value_counts(sort=False).tolist(), "days>=2", int((pp.days>=2).sum()))
