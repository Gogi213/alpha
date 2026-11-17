import pickle, json, time, numpy as np, pandas as pd
cfg = json.load(open("/root/rev534/syms.json")); coins = cfg["coins"]
tab = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb")); df = tab["df"]
X = df[(df.view=="z")&(df.th==50)&df.coin.isin(coins)].copy()
E = pd.read_pickle("/root/rev534/my_eps.pkl"); P = pd.read_pickle("/root/rev534/my_pairs.pkl")
B = pickle.load(open("/root/rev534/my_books.pkl","rb")); books, trades = B["books"], B["trades"]
print("== counts before filters, 22 coins, theta 0.5% from zero, strict")
for lab, ms in (("1s",1000),("5s",5000),("60s",60000),("10min",600000)):
    print(f">= {lab}: mine {int((E.dur>=ms).sum())}  executor {int((X.dur>=ms).sum())}")
key = ["coin","exA","exB","side","t"]
m = E.merge(X[key+["dur","f_stale","f_stale_price","f_contract","f_gap","f_tradfi","f_delist","f_funding","f_mark","layer","keep","keep_p","pair_Mmed","raw_gapA","raw_gapB","edgemax"]], on=key, how="outer", suffixes=("","_x"), indicator=True)
print("episode match:", m._merge.value_counts().to_dict())
b = m[m._merge=="both"]
print("dur exact:", int((b.dur==b.dur_x).sum()), "of", len(b), " max |ddur| ms", float((b.dur-b.dur_x).abs().max()))
for side in ("left_only","right_only"):
    o = m[m._merge==side]
    if len(o): print(side, "by pair:", o.groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(8).to_dict(), " dur q50", o[["dur","dur_x"]].max(axis=1).median())
pc = X.groupby(["coin","exA","exB"]).agg(n5=("dur", lambda s:(s>=5000).sum()))
pm = E.groupby(["coin","exA","exB"]).agg(n5=("dur", lambda s:(s>=5000).sum()))
j = pc.join(pm, lsuffix="_x", how="outer").fillna(0); bad = j[j.n5 != j.n5_x]
print("pairs:", len(j), " pairs with differing >=5s counts:", len(bad)); print(bad.head(15).to_string())
# filters on matched
b = b.copy()
b["my_stale"] = (b.silA>5000)|(b.silB>5000)
b["my_contract"] = b.mmed.abs()>=25
print("stale agreement (>=5s):", pd.crosstab(b.my_stale[b.dur>=5000], b.f_stale[b.dur>=5000]).to_dict())
print("contract agreement (>=5s):", pd.crosstab(b.my_contract[b.dur>=5000], b.f_contract[b.dur>=5000]).to_dict(), " |mmed - pair_Mmed| max", float((b.mmed-b.pair_Mmed).abs().max()))
oth = b[["f_gap","f_tradfi","f_delist","f_funding","f_mark"]].any(axis=1)
b["my_keep"] = ~b.my_stale & ~b.my_contract & ~oth
print("== after filters (own stale+contract, executor gap/tradfi/delist/funding/mark)")
for lab, ms in (("5s",5000),("60s",60000),("10min",600000)):
    s = b[b.dur>=ms]; print(f">= {lab}: mine {int(s.my_keep.sum())} executor(raw reading) {int(s.keep.sum())}")
print("INIT/SOPH/KAVA keep >=5s by pair (mine):", b[(b.dur>=5000)&b.my_keep].groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(12).to_dict())
print("executor keep >=5s by pair:", b[(b.dur>=5000)&b.keep].groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(12).to_dict())
# time-weighted vs unweighted median
print("pairs mmed vs mmed_tw:", P.assign(d=(P.mmed-P.mmed_tw).abs()).sort_values("d",ascending=False).head(8).round(1).to_string())
# trades during silence of stale-removed episodes
print("== trades during the silent leg's silence (stale-removed, >=5s)")
res = []
for r in b[(b.dur>=5000)&b.my_stale].itertuples():
    for leg, ex, sil in (("A", r.exA, r.silA), ("B", r.exB, r.silB)):
        if sil <= 5000: continue
        e, bid, ask, _, _ = books[(r.coin, ex)]
        t0, t1 = r.t, r.t + r.dur
        lo = max(np.searchsorted(e, t0, "right") - 1, 0); hi = np.searchsorted(e, t1, "left")
        seg = e[lo:hi+1]; g = np.diff(seg); k = int(np.argmax(g)); a0, a1 = seg[k], seg[k+1]
        st_bid, st_ask = bid[lo+k], ask[lo+k]
        te, tp = trades[(r.coin, ex)]
        i0, i1 = np.searchsorted(te, a0, "right"), np.searchsorted(te, a1, "left")
        p = tp[i0:i1]
        res.append((r.coin, ex, sil/1000, len(p), int(((p < st_bid*(1-1e-9)) | (p > st_ask*(1+1e-9))).sum()), r.dur/1000, r.layer))
R = pd.DataFrame(res, columns=["coin","ex","sil_s","ntr","nout","dur_s","layer"])
R["bucket"] = pd.cut(R.sil_s, [5,10,30,60,600,3600,1e9])
print(R.groupby("bucket", observed=True).agg(n=("ntr","size"), with_trades=("ntr", lambda s:(s>0).mean()), trades_outside_quote=("nout", lambda s:(s>0).mean())).round(3).to_string())
print(R.groupby("ex").agg(n=("ntr","size"), with_trades=("ntr", lambda s:(s>0).mean()), outside=("nout", lambda s:(s>0).mean()), sil_q50=("sil_s","median")).round(3).to_string())
R.to_csv("/root/rev534/stale_trades.csv", index=False)
# INIT per-day median M per pair
print("== INIT per-day median mid gap (bps), from raw")
DAY=86400000; TZ=4*3600000
for (c,a,bb) in [k for k in P[P.coin=="INIT"][["coin","exA","exB"]].itertuples(index=False)]:
    eA,bA,aA,_,_ = books[(c,a)]; eB,bB,aB,_,_ = books[(c,bb)]
    T = np.union1d(eA,eB); T=T[(T>=cfg["t0"])&(T<cfg["t1"])]
    ia=np.searchsorted(eA,T,"right")-1; ib=np.searchsorted(eB,T,"right")-1; ok=(ia>=0)&(ib>=0); T,ia,ib=T[ok],ia[ok],ib[ok]
    M = ((bA[ia]+aA[ia])/(bB[ib]+aB[ib])-1)*1e4; day=(T+TZ)//DAY
    print(a,bb, {time.strftime("%m-%d", time.gmtime(int(dd)*86400)): round(float(np.median(M[day==dd])),1) for dd in np.unique(day)})
